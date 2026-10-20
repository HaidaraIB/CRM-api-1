# Object storage (Cloudflare R2)

Every file the CRM keeps lives in Cloudflare R2, reached through the S3 API with `django-storages`. That covers attachments, the company library, photos, logos, backups and call recordings.

When no R2 endpoint is set, everything falls back to local disk under `MEDIA_ROOT`. Dev and CI run that way.

Code: `object_storage/` (`config.py`, `backends.py`, `presign.py`, `paths.py`, `serving.py`, and the management commands).

## Buckets and aliases

| `STORAGES` alias | Bucket (default name) | Holds | Access |
|---|---|---|---|
| `default` | `loop-crm-media` | Tenant media: chat, WhatsApp, social and support attachments; company library; profile and field-visit photos; template header media; Meta avatars; invoice logo | Private, presigned |
| `public` | `R2_PUBLIC_BUCKET`, otherwise `default` | Guide and news cover images (platform content) | Public via its custom domain; private if no public bucket is set |
| `backups` | `R2_BACKUP_BUCKET`, otherwise `default` | Database backups (`backups/…`) | Private, presigned |
| `recordings` | `loop-pbx-recordings` | PBX call recordings (uploaded by the PBX node, CRM-pbx contract §7) and WhatsApp call recordings | Private, presigned (5 min) |

**Rule: everything tenant-owned is private.** R2 has no object ACLs, so a private object is only ever read through a short-lived presigned URL. Only platform content images may sit in a public bucket.

**Keys.** Every tenant object sits under its company:

- `companies/<company_id>/<folder>/…`, from `TenantUploadTo` in `object_storage/paths.py`. Examples:
  - `companies/12/tenant_chat/2026/10/17/photo.png`
  - `companies/12/social_profiles/fb_123.jpg`
- `telephony/<company_id>/…` for PBX recordings.
- `recording/<company_id>/…` for WhatsApp call recordings.
- `platform/…` for objects with no company: a platform admin's photo, the invoice logo.

Files stored before this change keep their old keys, such as `profile_photos/x.jpg`. The migration copies them unchanged.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `OBJECT_STORAGE_BACKEND` | `r2` if `R2_ENDPOINT_URL` or `R2_ACCOUNT_ID` is set, else `local` | `local` or `r2`, for media, public and backups |
| `RECORDING_STORAGE_BACKEND` | same as above | `local` or `r2` (`s3` accepted) for recordings |
| `R2_ACCOUNT_ID` | — | Gives the endpoint `https://<id>.r2.cloudflarestorage.com` |
| `R2_ENDPOINT_URL` | — | Explicit endpoint (dev: `http://127.0.0.1:9000`). Overrides the account id. |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | — | R2 API token. `R2_SECRET_ACCESS_KEY_FILE` reads the secret from a file. |
| `R2_REGION` | `auto` | SigV4 region (R2 always uses `auto`) |
| `R2_MEDIA_BUCKET` | `loop-crm-media` | Private media |
| `R2_PUBLIC_BUCKET` / `R2_PUBLIC_DOMAIN` | — | Public bucket for platform content, and its custom domain (or `pub-….r2.dev` host). Each needs the other. |
| `R2_BACKUP_BUCKET` | the media bucket | Backups |
| `R2_RECORDINGS_BUCKET` | `loop-pbx-recordings` | Call recordings |
| `R2_MEDIA_URL_TTL` | `21600` (6 h) | Minimum lifetime of a media URL handed to a client |
| `R2_URL_WINDOW` | `3600` (1 h) | Media URLs are identical within this window (see below) |

**Old names still work as fallbacks.** Prefer the `R2_*` names in new deployments:

| Old name | Read as |
|---|---|
| `RECORDING_S3_ENDPOINT_URL` | endpoint |
| `RECORDING_S3_REGION` | region |
| `TELEPHONY_RECORDING_BUCKET` or `RECORDING_S3_BUCKET` | recordings bucket |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | credentials |

**Credentials.** They are read explicitly and passed to the client. boto3's ambient chain (shared config files, instance metadata, `AWS_SESSION_TOKEN`) is never used.

**Fail fast.** A half-configured R2 setup fails at startup with `ImproperlyConfigured`, for example an endpoint without credentials, or a public bucket without a domain.

**Secrets in logs.** Credentials are never logged and `R2Settings.__repr__` hides them. Logs carry only bucket, key and operation.

## URLs

- **Media URLs** (`field.url`, for example `profile_photo` in API responses) are presigned GETs. They are signed with the time rounded down to `R2_URL_WINDOW` and live for `R2_MEDIA_URL_TTL + R2_URL_WINDOW` (`object_storage/presign.py`). As a result:
  - every response within one hour carries the **same** URL for a file, so browser caches, Flutter's image cache and React Query's structural sharing all keep working;
  - a URL handed out at any moment stays valid for at least 6 h, longer than any client holds a response (React Query's global `staleTime` is 60 s and lists refetch on focus).
- **Downloads behind an access check** answer `302` to a presigned URL that carries `response-content-disposition` and `response-content-type`, with `Cache-Control: private, no-store` on the redirect. This covers the team, support, WhatsApp and social chat attachment endpoints, the library download, the backup download and WhatsApp recording playback. Bytes never pass through a gunicorn slot. On local disk the same endpoints stream the file.
- **PBX recording playback** (`POST /telephony/recordings/<id>/access/`) returns a 5-minute presigned URL, signed at the current second. Every issue is audit-logged.

## Clients

| Client | How it uses file URLs |
|---|---|
| CRM-project | Images use `<img src>` directly. Chat media, recordings and library files are `fetch()`ed from the API endpoint with the JWT, then turned into blobs. The browser follows the 302 to R2 **without** the `Authorization` header, which is stripped on a cross-origin redirect. `fetchChatMediaBlob` sends the token only to the API origin (`utils/apiOrigin.ts`), and its blob cache is keyed by the API path. |
| CRM-admin-panel | The backup download `fetch()`es the API with the JWT, follows the 302, and reads `Content-Disposition` for the file name. Support chat media uses `fetchChatMediaBlob`, which sends the token only to the API origin (`services/apiOrigin.ts`). |
| crm_mobile | Avatars and photos use `StableNetworkImage` (`lib/utils/stable_network_image.dart`), whose cache key is the URL without the signature. `fetchAuthenticatedBinaryGet` sends the token only to the API origin. Dart drops `Authorization` on a cross-origin redirect, and R2 rejects a request carrying both a header and a query signature. |

**Persisted URLs.** CRM-project keeps the current user, including the presigned `profile_photo` URL, in `localStorage`, but re-reads it from `/users/me/` on every load. A tab left open past the URL's lifetime keeps showing the avatar from the browser cache: objects carry `Cache-Control: private, max-age=86400`. Never store a presigned URL in the database; store the key (`SocialContact.avatar_path`, not `profile_pic_url`).

## CORS (R2 bucket settings)

`<img>`, `<audio>`, `<a>` and Flutter need no CORS. Browser `fetch()`es that follow the API's redirect do. After a cross-origin redirect the browser sends **`Origin: null`**, so a list of app origins cannot match. The rule therefore allows `*`. It covers reads only, and the presigned signature is still required.

Apply this to `loop-crm-media`, `loop-pbx-recordings` and the backups bucket if it is separate:

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["Content-Disposition", "Content-Length", "Content-Type", "ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

The public bucket only needs the same rule if a page `fetch()`es covers; today none does.

The dev SeaweedFS answers every origin with `*`, so dev cannot show a missing R2 rule. Run `check_object_storage` and open a chat image after changing the R2 settings.

## Operator setup (production)

1. **Create the buckets** in the R2 dashboard, in one location hint near the CRM VPS:
   - `loop-crm-media` (private)
   - `loop-pbx-recordings` (private; it may already exist for the PBX)
   - optionally `loop-crm-backups` (private) and `loop-crm-public`
2. **Public bucket** (if used): connect a custom domain such as `media.<domain>`. Do not enable r2.dev public access on any other bucket.
3. **API token for the CRM:** *Object Read & Write*, scoped to the buckets above.
   - The PBX node keeps its own token, scoped to `loop-pbx-recordings` only (CRM-pbx `LOOP_S3_*`).
4. **CORS:** apply the rule above to the private buckets.
5. **No lifecycle rule on `loop-pbx-recordings`.** It would delete recordings under legal hold. Retention is `purge_pbx_recordings` (cron #22). A lifecycle rule on `_healthcheck/` objects is harmless but not needed.
6. **Set the env** on the CRM VPS, then restart gunicorn, the qcluster and cron:
   - `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (or `_FILE`);
   - the bucket names if they are not the defaults;
   - `R2_PUBLIC_BUCKET` / `R2_PUBLIC_DOMAIN` if a public bucket is used.
7. **Run `manage.py check_object_storage`.** For each bucket it puts a probe, reads it back through a presigned URL (checking the Content-Disposition override), reads it through the API and deletes it. This is the real check of R2 compatibility; tests never touch R2.
8. **Migrate** the existing files (below).

## Migration runbook

```sh
manage.py migrate_media_to_r2                          # dry run: counts what would be copied, per bucket
manage.py migrate_media_to_r2 --apply                  # copy MEDIA_ROOT into R2, same keys
```

Where each file goes:

| Source | Target |
|---|---|
| `MEDIA_ROOT/pbx_recordings/<key>` | `recordings` at `<key>` |
| guide and news covers (and anything under `platform_content/`) | `public` |
| `backups/` | `backups` |
| everything else | `default` |

The command never deletes a source file:

- `fonts/` and `backups/pre-restore-*` (local working files) are skipped.
- It is idempotent: an object already present with the same size is counted `present` and not copied.
- A failed object makes the run exit non-zero; re-run to retry.

Order of operations:

1. Deploy with the R2 env set, then run `check_object_storage`.
2. Run the media migration with `--apply`. New uploads already go to R2, so this can run while the app serves traffic. Old URLs keep working because the keys are unchanged.
3. Keep `MEDIA_ROOT` until spot checks pass (a chat image, a library download, an avatar, a backup download, an invoice PDF with its logo), then archive it.

## R2 compatibility

| Concern | How it is handled |
|---|---|
| Checksums | boto3 ≥ 1.36 adds CRC32 checksum headers to uploads by default, and R2 has answered them with "We encountered an internal error". The client uses `request_checksum_calculation` and `response_checksum_validation` set to `"when_required"` (`r2_client_config`). The dev SeaweedFS accepts both settings, so only `check_object_storage` against R2 proves it. |
| Addressing | Path-style for boto3 (`s3.addressing_style = "path"`) and for the PBX worker's stdlib signer. Verified live against SeaweedFS: the worker uploads with region `auto`, and the CRM presigns and reads the same object. |
| Presigning | `presign.py` matches botocore byte for byte, including Arabic keys and response overrides (`tests/test_object_storage.py`). Lifetimes are at most 7 days. |
| Overwrites | `file_overwrite=False`: two uploads named `photo.jpg` get distinct keys, as on local disk. |

## Tests

- **Unit, no network** (`tests/test_object_storage.py`, part of the normal suite): configuration, presigning against botocore, keys, serving, backups, the invoice logo, avatars and the migration command. They use `InMemoryStorage`.
- **Live against the dev SeaweedFS** (never real R2):

  ```sh
  docker compose -f ../CRM-pbx/deploy/dev/docker-compose.yml up -d s3
  LOOP_STORAGE_LIVE=1 .\.venv\Scripts\python.exe -m pytest tests/test_object_storage_live.py -n 0
  ```

  It covers upload, presign, GET and delete plus `check_object_storage`; a chat attachment redirect fetched with `Origin: null`; a backup round trip; a recording uploaded by the PBX worker's own signer and played through the CRM; and the migration command. Each run creates and removes its own buckets.
- **Real browser:** `CRM-pbx/devtools/webrtc/crm_ui.mjs` runs the CRM on SeaweedFS-backed storage. It checks a Team Chat image (fetch → 302 → bucket) and a presigned avatar.
