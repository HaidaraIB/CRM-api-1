"""
Repair WhatsApp Tech Provider webhook subscription + Cloud API registration.

Run: .venv\\Scripts\\python.exe manage.py whatsapp_repair_subscriptions
     .venv\\Scripts\\python.exe manage.py whatsapp_repair_subscriptions --company-id 3
     .venv\\Scripts\\python.exe manage.py whatsapp_repair_subscriptions --register
"""
from django.core.management.base import BaseCommand

from integrations.models import WhatsAppAccount
from integrations.services.whatsapp_coexistence import (
    fetch_phone_registration_fields,
    fetch_waba_subscribed_apps,
    register_cloud_phone_number,
    subscribe_waba_webhooks,
)


class Command(BaseCommand):
    help = "POST subscribed_apps for connected WABAs; optionally /register Cloud API numbers."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, default=None)
        parser.add_argument(
            "--register",
            action="store_true",
            help="Also POST /{phone_number_id}/register for non-coexistence numbers.",
        )
        parser.add_argument(
            "--pin",
            type=str,
            default="",
            help="Optional 6-digit PIN for /register (reused per phone when omitted).",
        )

    def handle(self, *args, **options):
        qs = WhatsAppAccount.objects.filter(status="connected").select_related(
            "company", "integration_account"
        )
        company_id = options.get("company_id")
        if company_id:
            qs = qs.filter(company_id=company_id)

        rows = list(qs.order_by("id"))
        if not rows:
            self.stdout.write(self.style.WARNING("No connected WhatsAppAccount rows."))
            return

        do_register = bool(options.get("register"))
        pin_arg = (options.get("pin") or "").strip()
        seen_wabas = set()

        for wa in rows:
            token = wa.get_access_token()
            if not token and wa.integration_account_id:
                token = wa.integration_account.get_access_token()
            self.stdout.write(
                f"id={wa.id} company={wa.company_id} phone_number_id={wa.phone_number_id} "
                f"waba_id={wa.waba_id or '-'} display={wa.display_phone_number or '-'}"
            )
            if not token:
                self.stdout.write(self.style.ERROR("  no access token — skip"))
                continue

            fields = fetch_phone_registration_fields(token, wa.phone_number_id)
            if fields:
                self.stdout.write(
                    f"  phone status={fields.get('status')} platform={fields.get('platform_type')} "
                    f"is_on_biz_app={fields.get('is_on_biz_app')}"
                )

            waba = (wa.waba_id or "").strip()
            if waba and waba not in seen_wabas:
                seen_wabas.add(waba)
                ok = subscribe_waba_webhooks(token, waba)
                self.stdout.write(
                    self.style.SUCCESS(f"  subscribed_apps POST waba={waba}: ok")
                    if ok
                    else self.style.ERROR(f"  subscribed_apps POST waba={waba}: FAILED")
                )
                listed = fetch_waba_subscribed_apps(token, waba)
                if listed.get("error"):
                    self.stdout.write(f"  subscribed_apps GET error: {listed['error']}")
                else:
                    self.stdout.write(
                        f"  subscribed_apps GET status={listed.get('status_code')} "
                        f"body={str(listed.get('body'))[:300]}"
                    )
            elif not waba:
                self.stdout.write(self.style.ERROR("  missing waba_id — reconnect in CRM"))

            if do_register:
                is_coex = bool(fields and fields.get("is_on_biz_app") is True)
                if is_coex:
                    self.stdout.write("  skip /register (coexistence / is_on_biz_app)")
                    continue
                existing = None
                acc = wa.integration_account
                if acc:
                    pins = (acc.metadata or {}).get("cloud_api_two_step_pins") or {}
                    existing = pins.get(str(wa.phone_number_id)) or pin_arg or None
                result = register_cloud_phone_number(token, wa.phone_number_id, pin=existing or pin_arg or None)
                if result.get("ok"):
                    self.stdout.write(self.style.SUCCESS("  /register ok"))
                    if acc and result.get("pin"):
                        meta = dict(acc.metadata or {})
                        pins = dict(meta.get("cloud_api_two_step_pins") or {})
                        pins[str(wa.phone_number_id)] = result["pin"]
                        meta["cloud_api_two_step_pins"] = pins
                        acc.metadata = meta
                        if acc.status == "error":
                            acc.status = "connected"
                            acc.error_message = None
                        acc.save(update_fields=["metadata", "status", "error_message", "updated_at"])
                else:
                    self.stdout.write(
                        self.style.ERROR(f"  /register failed: {result.get('error') or result.get('body')}")
                    )

        self.stdout.write(self.style.SUCCESS("Done."))
