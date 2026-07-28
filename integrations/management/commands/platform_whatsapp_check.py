"""
Print Platform / Company WhatsApp readiness (no secrets).
Run: .venv\\Scripts\\python.exe manage.py platform_whatsapp_check
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.platform_whatsapp import (
    effective_admin_template_lang,
    effective_admin_template_name,
    effective_otp_template_name,
    effective_platform_access_token,
    effective_platform_phone_number_id,
    platform_access_token_looks_valid,
    platform_whatsapp_configured,
)
from integrations.models import WhatsAppAccount


class Command(BaseCommand):
    help = "Check Platform WhatsApp (Company WhatsApp / OTP) configuration without printing secrets."

    def handle(self, *args, **options):
        api_base = (getattr(settings, "API_BASE_URL", None) or "").rstrip("/")
        webhook = f"{api_base or 'https://YOUR_API_HOST'}/api/integrations/webhooks/whatsapp/"

        self.stdout.write("Company WhatsApp (admin -> tenant owner) uses PLATFORM credentials, not tenant Embedded Signup.")
        self.stdout.write("")
        self.stdout.write(f"Callback URL (same as tenant WhatsApp): {webhook}")
        if not api_base or "localhost" in api_base:
            self.stdout.write(
                self.style.WARNING(
                    "  API_BASE_URL is localhost or empty - Meta cannot deliver production webhooks here. "
                    "Use your public HTTPS API origin."
                )
            )

        pid = effective_platform_phone_number_id()
        tok = effective_platform_access_token()
        self.stdout.write("")
        self.stdout.write(
            "Platform phone_number_id: "
            + (f"set (len={len(pid)})" if pid else self.style.ERROR("missing"))
        )
        self.stdout.write(
            "Platform access token: "
            + (f"set (len={len(tok)})" if tok else self.style.ERROR("missing"))
        )
        if platform_whatsapp_configured() and not platform_access_token_looks_valid():
            self.stdout.write(
                self.style.ERROR(
                    "  Token looks invalid (too short). Paste a Meta System User permanent token "
                    "(usually starts with EAA, 100+ chars) in Admin -> Settings -> Platform WhatsApp "
                    "or PLATFORM_WHATSAPP_ACCESS_TOKEN."
                )
            )
        elif platform_whatsapp_configured():
            self.stdout.write(self.style.SUCCESS("  platform_whatsapp_configured: True"))
        else:
            self.stdout.write(self.style.ERROR("  platform_whatsapp_configured: False"))

        admin_tpl = effective_admin_template_name()
        self.stdout.write("")
        self.stdout.write(
            "Admin template (cold outbound): "
            + (admin_tpl or self.style.WARNING("not set - session text only (needs 24h window)"))
        )
        if admin_tpl:
            self.stdout.write(f"  language: {effective_admin_template_lang()}")
        raw_admin = (
            getattr(settings, "PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME", "") or ""
        ).strip()
        if raw_admin and raw_admin.isdigit():
            self.stdout.write(
                self.style.WARNING(
                    f"  Env PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME={raw_admin!r} is digit-only "
                    "(ignored). Use an approved Meta template name, e.g. admin_notify_1."
                )
            )

        otp = effective_otp_template_name()
        self.stdout.write(f"OTP template (signup): {otp or '(not set)'}")

        conflicts = 0
        if pid:
            conflicts = WhatsAppAccount.objects.filter(
                phone_number_id=pid, status="connected"
            ).count()
        self.stdout.write("")
        if conflicts:
            self.stdout.write(
                self.style.WARNING(
                    f"  {conflicts} connected tenant WhatsAppAccount row(s) reuse the platform "
                    "phone_number_id. Inbound is routed to Company WhatsApp first, but disconnect "
                    "those rows so tenants do not share LOOP's platform number."
                )
            )
        else:
            self.stdout.write("No connected tenant WhatsAppAccount on platform phone_number_id.")

        self.stdout.write("")
        self.stdout.write("Next steps if send fails:")
        self.stdout.write("  1. Meta Business Suite -> System Users -> generate permanent token with whatsapp_business_messaging")
        self.stdout.write("  2. Save token + phone_number_id in Admin -> Settings -> Platform WhatsApp")
        self.stdout.write("  3. Approve a utility template with one {{1}} body param -> set admin_template_name")
        self.stdout.write("  4. Subscribe platform WABA to messages on the webhook URL above")
        self.stdout.write("  5. Company WhatsApp -> send; owner replies to the platform business number")
