"""
Print WhatsApp send/receive debugging facts (no secrets).
Run: .venv\\Scripts\\python.exe manage.py whatsapp_debug_check
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.models import WhatsAppAccount


class Command(BaseCommand):
    help = "Print WhatsApp webhook URL, env presence, and connected WhatsAppAccount rows (no tokens)."

    def handle(self, *args, **options):
        api_base = (getattr(settings, "API_BASE_URL", None) or "").rstrip("/")
        if not api_base:
            self.stdout.write(self.style.WARNING("API_BASE_URL is empty; set it to your public API origin."))
            api_base = "http://localhost:8000"

        webhook_path = "/api/integrations/webhooks/whatsapp/"
        send_path = "/api/integrations/whatsapp/send/"
        self.stdout.write("Meta Developer -> WhatsApp -> Configuration:")
        self.stdout.write(f"  Callback URL: {api_base}{webhook_path}")
        self.stdout.write("  Subscribe webhook field: messages (includes delivery status updates)")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "  graph_status=200 only means Meta ACCEPTED the message. Delivery failures arrive as "
                "status webhooks (failed). Check logs for 'WhatsApp message delivery failed'."
            )
        )

        wa_verify = getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "") or ""
        meta_verify = getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "") or ""
        effective_verify = wa_verify or meta_verify
        self.stdout.write(
            "Verify token: "
            + ("configured" if effective_verify else self.style.ERROR("missing - set WHATSAPP_WEBHOOK_VERIFY_TOKEN or META_WEBHOOK_VERIFY_TOKEN"))
        )

        wa_secret = getattr(settings, "WHATSAPP_CLIENT_SECRET", "") or ""
        meta_secret = getattr(settings, "META_CLIENT_SECRET", "") or ""
        self.stdout.write(
            "App secret for X-Hub-Signature-256: "
            + ("WHATSAPP_CLIENT_SECRET set" if wa_secret else "not set")
            + " / "
            + ("META_CLIENT_SECRET set" if meta_secret else "not set")
        )
        if not wa_secret and not meta_secret:
            self.stdout.write(self.style.ERROR("  Webhook POST signature verification will fail until one secret is set."))

        allowed = getattr(settings, "WHATSAPP_WEBHOOK_ALLOWED_IPS", None)
        if allowed:
            self.stdout.write(f"WHATSAPP_WEBHOOK_ALLOWED_IPS active: {allowed}")
            self.stdout.write(self.style.WARNING("  If behind a reverse proxy, REMOTE_ADDR must be the real client IP or webhooks get 403."))
        else:
            self.stdout.write("WHATSAPP_WEBHOOK_ALLOWED_IPS: not set (all IPs allowed after signature check)")

        es_cfg = getattr(settings, "WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID", "") or ""
        es_app = getattr(settings, "WHATSAPP_CLIENT_ID", "") or ""
        self.stdout.write("")
        self.stdout.write("Embedded Signup (FB SDK + config_id):")
        self.stdout.write(
            "  CRM connect flow uses Embedded Signup when WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID and WHATSAPP_CLIENT_ID are set."
        )
        if es_cfg and es_app:
            self.stdout.write(f"  config_id: {es_cfg[:8]}... (len={len(es_cfg)}) app_id: {es_app[:6]}...")
        else:
            self.stdout.write(self.style.WARNING("  Not enabled - add WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID in .env (see WHATSAPP_EMBEDDED_SIGNUP.md)."))

        self.stdout.write("")
        self.stdout.write(f"Authenticated send endpoint (Bearer token): POST {api_base}{send_path}")
        self.stdout.write("  Body: to (E.164 digits), message, optional phone_number_id, optional client_id")
        self.stdout.write("  On failure, API JSON details include graph_http_status when Graph returns an HTTP error.")
        self.stdout.write("")

        qs = WhatsAppAccount.objects.select_related("company").order_by("-id")[:100]
        count = WhatsAppAccount.objects.count()
        self.stdout.write(f"WhatsAppAccount rows (showing up to 100 of {count}):")
        if not count:
            self.stdout.write(self.style.WARNING("  No rows - connect WhatsApp in the app or run OAuth flow."))
            return

        for wa in qs:
            tok = wa.get_access_token()
            self.stdout.write(
                f"  id={wa.id} company_id={wa.company_id} "
                f"phone_number_id={wa.phone_number_id} status={wa.status} "
                f"has_token={bool(tok)} display={wa.display_phone_number or '-'}"
            )
            disp = (wa.display_phone_number or '').replace(' ', '').replace('-', '')
            if disp.startswith('+1555') or disp.startswith('1555'):
                self.stdout.write(
                    self.style.WARNING(
                        "    ^ Meta sandbox/test number (+1 555-...). Add each recipient phone in "
                        "Meta Developer Console -> WhatsApp -> API Setup -> test numbers, or complete "
                        "Business Verification for real delivery."
                    )
                )

        from integrations.models import LeadWhatsAppMessage
        inbound = LeadWhatsAppMessage.objects.filter(direction='inbound').count()
        outbound = LeadWhatsAppMessage.objects.filter(direction='outbound').count()
        self.stdout.write("")
        failed = LeadWhatsAppMessage.objects.filter(direction='outbound', delivery_status='failed').count()
        self.stdout.write(f"LeadWhatsAppMessage totals: inbound={inbound} outbound={outbound} failed_delivery={failed}")
        if inbound == 0:
            self.stdout.write(
                self.style.WARNING(
                    "  No inbound messages in DB - Meta webhooks must POST to your PUBLIC API URL "
                    "(not localhost). Check Meta Developer -> WhatsApp -> Configuration -> Webhook."
                )
            )
