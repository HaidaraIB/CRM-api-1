"""
Issuer / platform branding for invoice PDF and email (BillingSettings + logo).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings as dj_settings

logger = logging.getLogger(__name__)

_LOGO_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


@dataclass(frozen=True)
class IssuerBranding:
    issuer_name: str
    issuer_address: str
    issuer_email: str
    issuer_phone: str
    issuer_tax_id: str
    footer_text: str
    payment_instructions: str
    logo_bytes: bytes | None
    logo_mime: str | None
    platform_name: str

    @classmethod
    def load(cls) -> IssuerBranding:
        pn = (
            getattr(dj_settings, "PLATFORM_EMAIL_SENDER_DISPLAY_NAME", "LOOP CRM") or "LOOP CRM"
        ).strip() or "LOOP CRM"
        try:
            from settings.models import BillingSettings

            bs = BillingSettings.get_settings()
            logo_bytes: bytes | None = None
            logo_mime: str | None = None
            if bs.logo and getattr(bs.logo, "name", None):
                logo_mime = _LOGO_MIME.get(Path(bs.logo.name).suffix.lower())
                if logo_mime:
                    try:
                        # Through the storage: local disk or the private R2 bucket.
                        with bs.logo.storage.open(bs.logo.name, "rb") as fh:
                            logo_bytes = fh.read()
                    except Exception as e:  # missing file, storage unreachable
                        logger.warning("Could not read billing logo for invoice: %s", type(e).__name__)
                        logo_mime = None
            return cls(
                issuer_name=bs.issuer_name or "",
                issuer_address=bs.issuer_address or "",
                issuer_email=bs.issuer_email or "",
                issuer_phone=bs.issuer_phone or "",
                issuer_tax_id=bs.issuer_tax_id or "",
                footer_text=bs.footer_text or "",
                payment_instructions=bs.payment_instructions or "",
                logo_bytes=logo_bytes,
                logo_mime=logo_mime,
                platform_name=pn,
            )
        except Exception as e:
            logger.debug("IssuerBranding.load: %s", e)
            return cls("", "", "", "", "", "", "", None, None, pn)
