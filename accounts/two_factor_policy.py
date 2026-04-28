import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from .models import OwnerTrustedDevice


OWNER_TRUST_COOKIE_NAME = "owner_trusted_device"
OWNER_TRUST_DAYS = 7


def is_company_owner(user) -> bool:
    company = getattr(user, "company", None)
    return bool(company and getattr(company, "owner_id", None) == getattr(user, "id", None))


def hash_user_agent(user_agent: str) -> str:
    normalized = (user_agent or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_device_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def get_request_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def issue_trusted_device(user, request):
    raw_token = secrets.token_urlsafe(48)
    ua_hash = hash_user_agent(request.META.get("HTTP_USER_AGENT", ""))
    token_hash = hash_device_token(raw_token)
    now = timezone.now()
    trusted_until = now + timedelta(days=OWNER_TRUST_DAYS)

    OwnerTrustedDevice.objects.update_or_create(
        user=user,
        token_hash=token_hash,
        defaults={
            "user_agent_hash": ua_hash,
            "ip_address": get_request_ip(request),
            "trusted_until": trusted_until,
            "revoked_at": None,
            "last_seen_at": now,
        },
    )
    return raw_token, trusted_until


def is_trusted_device_valid(user, request) -> bool:
    raw_token = request.COOKIES.get(OWNER_TRUST_COOKIE_NAME, "").strip()
    if not raw_token:
        return False

    ua_hash = hash_user_agent(request.META.get("HTTP_USER_AGENT", ""))
    token_hash = hash_device_token(raw_token)
    now = timezone.now()
    trusted_device = (
        OwnerTrustedDevice.objects.filter(
            user=user,
            token_hash=token_hash,
            revoked_at__isnull=True,
            trusted_until__gt=now,
        )
        .order_by("-updated_at")
        .first()
    )
    if not trusted_device:
        return False
    if trusted_device.user_agent_hash and trusted_device.user_agent_hash != ua_hash:
        return False

    trusted_device.last_seen_at = now
    trusted_device.ip_address = get_request_ip(request)
    trusted_device.save(update_fields=["last_seen_at", "ip_address", "updated_at"])
    return True
