"""
Issuer / platform branding for invoice PDF and email (BillingSettings + logo).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings as dj_settings

logger = logging.getLogger(__name__)


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
                path = Path(dj_settings.MEDIA_ROOT) / bs.logo.name
                if path.is_file():
                    try:
                        raw = path.read_bytes()
                        ext = path.suffix.lower()
                        if ext == ".png":
                            logo_mime = "image/png"
                        elif ext in (".jpg", ".jpeg"):
                            logo_mime = "image/jpeg"
                        elif ext == ".gif":
                            logo_mime = "image/gif"
                        else:
                            logo_mime = None
                        if logo_mime:
                            logo_bytes = raw
                    except OSError as e:
                        logger.warning("Could not read billing logo for invoice: %s", e)
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
