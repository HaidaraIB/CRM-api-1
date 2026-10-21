"""
Object storage (Cloudflare R2) without a network: configuration, presigning,
keys, serving, backups, the invoice logo, avatars and the migration command.

The live counterpart against the dev SeaweedFS is tests/test_object_storage_live.py.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from botocore.config import Config
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage, default_storage, storages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import FileResponse, HttpResponseRedirect

from object_storage import config as cfg
from object_storage.backends import (
    R2Storage,
    backup_storage,
    is_remote,
    public_storage,
    r2_client_config,
    recordings_storage,
)
from object_storage.paths import TenantUploadTo, company_prefix
from object_storage.presign import presign_get_url
from object_storage.serving import file_response

R2 = cfg.R2Settings(endpoint_url="https://acc0unt.r2.cloudflarestorage.com", access_key_id="AKTEST",
                    secret_access_key="s3cr3t-value")
MEMORY = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
STATIC = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}


def in_memory_storages():
    return {"default": MEMORY, "public": MEMORY, "backups": MEMORY, "recordings": MEMORY, "staticfiles": STATIC}


# ---------------------------------------------------------------------------
# Presigning: identical to botocore, stable inside a window
# ---------------------------------------------------------------------------


def _botocore_url(*, endpoint, key, expires, at, params=None, region="auto"):
    client = boto3.client(
        "s3", endpoint_url=endpoint, region_name=region, aws_access_key_id="AKTEST",
        aws_secret_access_key="s3cr3t-value",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    with mock.patch("botocore.auth.get_current_datetime", return_value=at):
        return client.generate_presigned_url(
            "get_object", Params={"Bucket": "loop-crm-media", "Key": key, **(params or {})}, ExpiresIn=expires,
        )


def _parts(url):
    u = urlsplit(url)
    return u.scheme, u.netloc, u.path, {k: v for k, v in parse_qs(u.query).items()}


@pytest.mark.parametrize("endpoint", ["https://acc0unt.r2.cloudflarestorage.com", "http://127.0.0.1:9000"])
@pytest.mark.parametrize("key,params", [
    ("companies/7/tenant_chat/2026/10/17/photo.png", None),
    ("companies/7/company_library/2026/10/17/عقد الإيجار (1).pdf", {
        "ResponseContentDisposition": "attachment; filename*=utf-8''%D8%B9%D9%82%D8%AF.pdf",
        "ResponseContentType": "application/pdf",
    }),
    ("platform/billing/logo+v2~x.png", {"ResponseCacheControl": "private, max-age=60"}),
])
def test_presigned_url_matches_botocore_byte_for_byte(endpoint, key, params):
    at = dt.datetime(2026, 10, 17, 9, 41, 7)
    ours = presign_get_url(
        endpoint_url=endpoint, bucket="loop-crm-media", key=key, region="auto", access_key="AKTEST",
        secret_key="s3cr3t-value", expires=900, parameters=params,
        now=at.replace(tzinfo=dt.timezone.utc).timestamp(),
    )
    assert _parts(ours) == _parts(_botocore_url(endpoint=endpoint, key=key, expires=900, at=at, params=params))


def test_url_is_stable_inside_a_window_and_outlives_the_ttl():
    kwargs = dict(endpoint_url=R2.endpoint_url, bucket="b", key="k.png", region="auto", access_key="AK",
                  secret_key="s", expires=6 * 3600, window=3600)
    start = dt.datetime(2026, 10, 17, 9, 0, 0, tzinfo=dt.timezone.utc).timestamp()
    urls = {presign_get_url(**kwargs, now=start + offset) for offset in (0, 1, 1800, 3599)}
    assert len(urls) == 1  # one URL for the whole window: browser and image caches hit
    later = presign_get_url(**kwargs, now=start + 3600)
    assert later not in urls
    q = parse_qs(urlsplit(urls.pop()).query)
    assert q["X-Amz-Date"] == ["20261017T090000Z"] and q["X-Amz-Expires"] == [str(7 * 3600)]
    # Handed out at the very end of the window, it is still valid for the full TTL.
    assert start + 7 * 3600 - (start + 3599) >= 6 * 3600 - 1


def test_presign_rejects_lifetimes_r2_would_refuse():
    with pytest.raises(ValueError):
        presign_get_url(endpoint_url=R2.endpoint_url, bucket="b", key="k", region="auto", access_key="a",
                        secret_key="s", expires=7 * 24 * 3600, window=3600)
    with pytest.raises(ValueError):
        presign_get_url(endpoint_url=R2.endpoint_url, bucket="b", key="k", region="auto", access_key="a",
                        secret_key="s", expires=60, parameters={"ResponseFoo": "x"})


# ---------------------------------------------------------------------------
# Configuration: one set of env vars, local by default, old names still work
# ---------------------------------------------------------------------------


def test_nothing_set_means_local_disk_everywhere():
    storages_, media, recordings = cfg.storages_from_env(env={})
    assert (media, recordings) == ("local", "local")
    assert storages_["default"]["BACKEND"].endswith("FileSystemStorage")
    assert storages_["public"]["BACKEND"].endswith("FileSystemStorage")
    assert storages_["recordings"] == {"BACKEND": cfg.MEDIA_SUBDIR_BACKEND, "OPTIONS": {"subdir": "pbx_recordings"}}


def test_r2_account_id_turns_on_r2_with_private_media_and_a_public_bucket():
    env = {"R2_ACCOUNT_ID": "abc123", "R2_ACCESS_KEY_ID": "AK", "R2_SECRET_ACCESS_KEY": "SK",
           "R2_PUBLIC_BUCKET": "loop-crm-public", "R2_PUBLIC_DOMAIN": "https://media.loopcrm.app/"}
    storages_, media, recordings = cfg.storages_from_env(env=env)
    assert (media, recordings) == ("r2", "r2")
    default = storages_["default"]["OPTIONS"]
    assert default["endpoint_url"] == "https://abc123.r2.cloudflarestorage.com"
    assert default["region_name"] == "auto" and default["bucket_name"] == "loop-crm-media"
    assert default["file_overwrite"] is False and default["querystring_expire"] == 6 * 3600
    assert default["url_window"] == 3600
    public = storages_["public"]["OPTIONS"]
    assert public["bucket_name"] == "loop-crm-public" and public["custom_domain"] == "media.loopcrm.app"
    assert public["querystring_auth"] is False
    assert storages_["backups"]["OPTIONS"]["bucket_name"] == "loop-crm-media"  # private bucket by default
    rec = storages_["recordings"]["OPTIONS"]
    assert rec["bucket_name"] == "loop-pbx-recordings" and rec["url_window"] == 0 and rec["querystring_expire"] == 300


def test_without_a_public_bucket_covers_stay_private():
    storages_, _, _ = cfg.storages_from_env(env={"R2_ENDPOINT_URL": "http://127.0.0.1:9000", "R2_ACCESS_KEY_ID": "a",
                                                 "R2_SECRET_ACCESS_KEY": "b"})
    assert storages_["public"]["OPTIONS"]["bucket_name"] == "loop-crm-media"
    assert storages_["public"]["OPTIONS"].get("querystring_auth", True) is True


def test_old_recording_variables_still_work_and_keep_media_local():
    env = {"RECORDING_STORAGE_BACKEND": "s3", "RECORDING_S3_ENDPOINT_URL": "https://x.r2.cloudflarestorage.com",
           "RECORDING_S3_REGION": "auto", "TELEPHONY_RECORDING_BUCKET": "old-recordings",
           "AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK"}
    storages_, media, recordings = cfg.storages_from_env(env=env)
    assert (media, recordings) == ("local", "r2")
    rec = storages_["recordings"]["OPTIONS"]
    assert rec["endpoint_url"] == "https://x.r2.cloudflarestorage.com" and rec["bucket_name"] == "old-recordings"
    assert (rec["access_key"], rec["secret_key"]) == ("AK", "SK")  # read explicitly, then passed explicitly
    assert storages_["default"]["BACKEND"].endswith("FileSystemStorage")


def test_secret_can_come_from_a_file(tmp_path):
    secret = tmp_path / "r2-secret"
    secret.write_text("from-file\n")
    r2 = cfg.r2_settings_from_env({"R2_ENDPOINT_URL": "https://e", "R2_SECRET_ACCESS_KEY_FILE": str(secret)})
    assert r2.secret_access_key == "from-file"
    assert "from-file" not in repr(r2) and "secret" not in repr(r2).lower()


@pytest.mark.parametrize("env", [
    {"OBJECT_STORAGE_BACKEND": "r2"},  # no endpoint
    {"R2_ENDPOINT_URL": "https://e"},  # no credentials
    {"R2_ENDPOINT_URL": "https://e", "R2_ACCESS_KEY_ID": "a", "R2_SECRET_ACCESS_KEY": "b", "R2_PUBLIC_BUCKET": "p"},
    {"OBJECT_STORAGE_BACKEND": "minio"},
    {"R2_ENDPOINT_URL": "https://e", "R2_ACCESS_KEY_ID": "a", "R2_SECRET_ACCESS_KEY": "b", "R2_MEDIA_URL_TTL": "60"},
])
def test_half_configured_r2_fails_at_startup(env):
    with pytest.raises(ImproperlyConfigured):
        cfg.storages_from_env(env=env)


def test_r2_storage_never_uses_the_ambient_aws_chain(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AMBIENT")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AMBIENT-SECRET")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "AMBIENT-TOKEN")
    storage = R2Storage(**cfg.build_storages(media="r2", recordings="local", r2=R2)["default"]["OPTIONS"])
    assert (storage.access_key, storage.secret_key, storage.security_token) == ("AKTEST", "s3cr3t-value", None)
    bare = R2Storage(bucket_name="b", endpoint_url=R2.endpoint_url)
    assert (bare.access_key, bare.secret_key) == (None, None)
    creds = storage.connection.meta.client._request_signer._credentials
    assert (creds.access_key, creds.token) == ("AKTEST", None)


def test_r2_client_config_only_sends_checksums_when_required_and_uses_path_style():
    config = r2_client_config()
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"
    assert config.s3 == {"addressing_style": "path"} and config.signature_version == "s3v4"
    storage = R2Storage(**cfg.build_storages(media="r2", recordings="local", r2=R2)["default"]["OPTIONS"])
    client = storage.connection.meta.client
    assert client.meta.config.request_checksum_calculation == "when_required"
    assert client.meta.region_name == "auto"


def test_storage_url_is_the_stable_presigned_url():
    storage = R2Storage(**cfg.build_storages(media="r2", recordings="local", r2=R2)["default"]["OPTIONS"])
    url = storage.url("companies/1/profile_photos/a b.png")
    parts = urlsplit(url)
    assert parts.netloc == "acc0unt.r2.cloudflarestorage.com"
    assert parts.path == "/loop-crm-media/companies/1/profile_photos/a%20b.png"
    assert url == storage.url("companies/1/profile_photos/a b.png")
    assert "s3cr3t" not in url


def test_alias_handles_follow_storages_overrides(settings):
    assert not is_remote(public_storage())
    settings.STORAGES = cfg.build_storages(media="r2", recordings="r2", r2=R2)
    assert is_remote(public_storage()) and is_remote(backup_storage()) and is_remote(recordings_storage())
    assert recordings_storage().bucket_name == "loop-pbx-recordings"


def test_local_recordings_follow_media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    assert Path(storages["recordings"].location) == tmp_path / "pbx_recordings"


# ---------------------------------------------------------------------------
# Keys: every tenant object under its company
# ---------------------------------------------------------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_tenant_upload_to_prefixes_the_company():
    upload = TenantUploadTo("tenant_chat/%Y/%m/%d", company="conversation.company_id")
    key = upload(_Obj(conversation=_Obj(company_id=42)), "photo.png")
    assert key.startswith("companies/42/tenant_chat/") and key.endswith("/photo.png")
    assert TenantUploadTo("profile_photos")(_Obj(company_id=None), "me.jpg") == "platform/profile_photos/me.jpg"
    assert TenantUploadTo("x", company="client.company_id")(_Obj(client=None), "f") == "platform/x/f"
    assert company_prefix(7) == "companies/7"
    assert upload.deconstruct() == (
        "object_storage.paths.TenantUploadTo", ("tenant_chat/%Y/%m/%d",), {"company": "conversation.company_id"},
    )


def test_model_fields_key_tenant_files_under_the_company(company, owner_user):
    from accounts.models import User
    from company_library.models import CompanyLibraryFile
    from crm.models import ClientFieldVisit
    from integrations.models import LeadWhatsAppMessage, MessageTemplate, SocialMessage
    from platform_content.models import GuideArticle, NewsPost
    from settings.models import BillingSettings, SystemBackup
    from support.models import SupportTicketAttachment
    from support_chat.models import SupportMessage
    from tenant_chat.models import ChatMessage

    def upload_to(model, name):
        return model._meta.get_field(name).upload_to

    cases = {
        (User, "profile_photo"): "company_id",
        (CompanyLibraryFile, "file"): "company_id",
        (ClientFieldVisit, "client_location_photo"): "client.company_id",
        (LeadWhatsAppMessage, "attachment"): "client.company_id",
        (MessageTemplate, "header_media"): "company_id",
        (SocialMessage, "attachment"): "conversation.company_id",
        (SupportTicketAttachment, "file"): "ticket.company_id",
        (SupportMessage, "attachment"): "conversation.company_id",
        (ChatMessage, "attachment"): "conversation.company_id",
    }
    for (model, name), path in cases.items():
        field_upload = upload_to(model, name)
        assert isinstance(field_upload, TenantUploadTo) and field_upload.company == path, (model, name)
    # Real instances resolve their company.
    assert upload_to(User, "profile_photo")(owner_user, "a.png").startswith(f"companies/{company.id}/profile_photos/")
    # Platform objects: covers in the public storage, backups in the backups storage, logo under platform/.
    assert GuideArticle._meta.get_field("cover_image").storage is public_storage()
    assert NewsPost._meta.get_field("cover_image").storage is public_storage()
    assert SystemBackup._meta.get_field("file").storage is backup_storage()
    assert upload_to(BillingSettings, "logo") == "platform/billing/"


# ---------------------------------------------------------------------------
# Serving: redirect to a presigned URL on R2, stream on disk
# ---------------------------------------------------------------------------


def test_file_response_redirects_to_a_presigned_url_with_disposition():
    storage = R2Storage(**cfg.build_storages(media="r2", recordings="local", r2=R2)["default"]["OPTIONS"])
    resp = file_response(storage, "companies/3/tenant_chat/2026/10/17/a.pdf", filename="عقد.pdf",
                         content_type="application/pdf", as_attachment=True)
    assert isinstance(resp, HttpResponseRedirect) and resp["Cache-Control"] == "private, no-store"
    q = parse_qs(urlsplit(resp["Location"]).query)
    assert q["response-content-disposition"] == ["attachment; filename*=utf-8''%D8%B9%D9%82%D8%AF.pdf"]
    assert q["response-content-type"] == ["application/pdf"]


def test_file_response_streams_from_local_storage():
    storage = InMemoryStorage()
    name = storage.save("companies/3/x.txt", ContentFile(b"hello"))
    resp = file_response(storage, name, filename="x.txt", content_type="text/plain")
    assert isinstance(resp, FileResponse) and b"".join(resp.streaming_content) == b"hello"
    assert resp["Content-Disposition"] == 'inline; filename="x.txt"'
    with pytest.raises(FileNotFoundError):
        file_response(storage, "missing.txt", filename="m.txt")


def test_backup_download_redirects_on_r2(settings, api_client, db):
    from accounts.models import User
    from settings.models import SystemBackup

    settings.STORAGES = cfg.build_storages(media="r2", recordings="local", r2=R2)
    admin = User.objects.create_user(username="sa", email="sa@example.com", password="x", is_superuser=True,
                                     is_staff=True)
    backup = SystemBackup.objects.create(file="backups/db-20261017.sqlite3", status=SystemBackup.Status.COMPLETED)
    api_client.force_authenticate(admin)
    resp = api_client.get(f"/api/v1/settings/backups/{backup.pk}/download/")
    assert resp.status_code == 302, resp.content
    location = urlsplit(resp["Location"])
    assert location.path == "/loop-crm-media/backups/db-20261017.sqlite3"
    assert parse_qs(location.query)["response-content-disposition"] == ['attachment; filename="db-20261017.sqlite3"']


# ---------------------------------------------------------------------------
# Backups through the storage API, restore included
# ---------------------------------------------------------------------------


def _sqlite(path: Path, value: str):
    con = sqlite3.connect(path)
    con.execute("create table if not exists t (v text)")
    con.execute("delete from t")
    con.execute("insert into t values (?)", (value,))
    con.commit()
    con.close()


def _value(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return con.execute("select v from t").fetchone()[0]
    finally:
        con.close()


def test_backup_round_trip_through_the_backups_storage(settings, tmp_path, db):
    from settings import services
    from settings.models import SystemBackup

    settings.STORAGES = in_memory_storages()
    settings.BACKUP_ROOT = tmp_path / "work"
    live = tmp_path / "live.sqlite3"
    _sqlite(live, "before")
    with mock.patch.object(services, "_ensure_sqlite_backend", return_value=(live, "sqlite3")):
        backup = services.create_database_backup(notes="t")
        storage = backup_storage()
        assert backup.status == SystemBackup.Status.COMPLETED and backup.file.name.startswith("backups/live-")
        assert storage.exists(backup.file.name) and backup.file_size == storage.size(backup.file.name)
        assert not (tmp_path / "work").exists() or not list((tmp_path / "work").glob("live-*"))  # nothing left on disk

        _sqlite(live, "after")
        snapshot = services.restore_database_backup(backup)
        assert _value(live) == "before"  # restored from the stored copy
        assert snapshot.parent == tmp_path / "work" and _value(snapshot) == "after"

        services.delete_backup(backup)
        assert not storage.exists(backup.file.name) and not SystemBackup.objects.filter(pk=backup.pk).exists()


def test_restore_reports_a_backup_missing_from_storage(settings, tmp_path, db):
    from settings import services
    from settings.models import SystemBackup

    settings.STORAGES = in_memory_storages()
    settings.BACKUP_ROOT = tmp_path
    live = tmp_path / "live.sqlite3"
    _sqlite(live, "keep")
    backup = SystemBackup.objects.create(file="backups/gone.sqlite3")
    with mock.patch.object(services, "_ensure_sqlite_backend", return_value=(live, "sqlite3")):
        with pytest.raises(FileNotFoundError):
            services.restore_database_backup(backup)
    assert _value(live) == "keep"


# ---------------------------------------------------------------------------
# Invoice logo and Meta avatars
# ---------------------------------------------------------------------------


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def test_invoice_logo_is_read_through_the_storage(settings, db):
    from settings.models import BillingSettings
    from subscriptions.invoicing.branding import IssuerBranding

    settings.STORAGES = in_memory_storages()
    bs = BillingSettings.get_settings()
    bs.logo.save("logo.png", ContentFile(PNG), save=True)
    assert bs.logo.name.startswith("platform/billing/")
    branding = IssuerBranding.load()
    assert branding.logo_bytes == PNG and branding.logo_mime == "image/png"


def test_presigned_avatar_urls_are_never_stored(settings):
    from integrations.services import meta_inbox_profile

    settings.STORAGES = cfg.build_storages(media="r2", recordings="local", r2=R2)
    assert meta_inbox_profile._persistable_url("companies/1/social_profiles/fb_1.jpg") == ""
    settings.STORAGES = in_memory_storages()
    settings.MEDIA_URL = "/media/"
    assert meta_inbox_profile._persistable_url("companies/1/social_profiles/fb_1.jpg").endswith(
        "/media/companies/1/social_profiles/fb_1.jpg"
    )


# ---------------------------------------------------------------------------
# migrate_media_to_r2
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_r2(settings, monkeypatch):
    """In-memory storages that the command accepts as object storage."""
    settings.STORAGES = in_memory_storages()
    monkeypatch.setattr("object_storage.management.commands.migrate_media_to_r2.is_remote", lambda s: True)
    return {alias: storages[alias] for alias in ("default", "public", "backups", "recordings")}


def _media_tree(root: Path):
    files = {
        "company_library/2026/01/02/contract.pdf": b"%PDF-1",
        "profile_photos/me.jpg": b"jpg",
        "platform_content/guide/2026/01/cover.png": b"png",
        "backups/db-1.sqlite3": b"SQLite format 3",
        "backups/pre-restore-1.sqlite3": b"snapshot",
        "pbx_recordings/telephony/1/2026/01/c/r.wav": b"RIFF",
        "fonts/x.ttf": b"font",
    }
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)


def _run(*args):
    from io import StringIO

    out = StringIO()
    call_command("migrate_media_to_r2", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def test_migration_is_a_dry_run_by_default_then_copies_once(fake_r2, tmp_path, db):
    _media_tree(tmp_path)
    dry = _run("--source", str(tmp_path))
    assert "DRY RUN" in dry and "scanned=5 copied=0 would_copy=5" in dry and "skipped=2" in dry
    assert not fake_r2["default"].exists("profile_photos/me.jpg")

    applied = _run("--source", str(tmp_path), "--apply")
    assert "copied=5" in applied and "failed=0" in applied
    assert fake_r2["default"].open("company_library/2026/01/02/contract.pdf").read() == b"%PDF-1"
    assert fake_r2["public"].exists("platform_content/guide/2026/01/cover.png")
    assert fake_r2["backups"].exists("backups/db-1.sqlite3")
    assert fake_r2["recordings"].exists("telephony/1/2026/01/c/r.wav")  # key relative to pbx_recordings/
    assert not fake_r2["backups"].exists("backups/pre-restore-1.sqlite3") and not fake_r2["default"].exists("fonts/x.ttf")

    again = _run("--source", str(tmp_path), "--apply")
    assert "copied=0" in again and "present=5" in again


def test_migration_refuses_a_local_target(settings, tmp_path, db):
    settings.STORAGES = in_memory_storages()
    _media_tree(tmp_path)
    with pytest.raises(CommandError, match="local disk"):
        _run("--source", str(tmp_path))
