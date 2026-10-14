"""
Reconnect inbox WhatsApp numbers that were disconnected by the CRM orphan cleanup.

A real disconnect also marks the whatsapp_inbox IntegrationAccount disconnected,
so this only restores rows whose integration is still connected and whose
metadata.phone_number_id matches the row.

  python manage.py repair_whatsapp_inbox_numbers            # dry run
  python manage.py repair_whatsapp_inbox_numbers --apply

After --apply, held inbound messages for each restored number are replayed.
"""
from django.core.management.base import BaseCommand

from integrations.models import IntegrationAccount, IntegrationPlatform, WhatsAppAccount
from integrations.services.whatsapp_unrouted import replay_after_connect


class Command(BaseCommand):
    help = "Restore inbox numbers disconnected while their integration stayed connected."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Default is a dry run.")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        restored = 0
        rows = (
            WhatsAppAccount.objects.inbox()
            .filter(status="disconnected", integration_account__isnull=True)
            .order_by("company_id", "id")
        )
        for row in rows:
            account = (
                IntegrationAccount.objects.filter(
                    company_id=row.company_id,
                    platform=IntegrationPlatform.WHATSAPP_INBOX,
                    status="connected",
                )
                .order_by("-updated_at")
            )
            match = None
            for candidate in account:
                pinned = str((candidate.metadata or {}).get("phone_number_id") or "").strip()
                if pinned and pinned == str(row.phone_number_id):
                    match = candidate
                    break
            if match is None:
                continue
            restored += 1
            self.stdout.write(
                f"company_id={row.company_id} phone_number_id={row.phone_number_id} "
                f"row_id={row.id} updated_at={row.updated_at.isoformat()} "
                f"integration_id={match.id}"
            )
            if not apply:
                continue
            token = match.get_access_token()
            row.integration_account = match
            row.status = "connected"
            row.error_message = None
            if token:
                row.set_access_token(token)
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  integration {match.id} has no access token; inbound routing will work, sends will not."
                    )
                )
            row.save()
            replay_after_connect(row.phone_number_id)

        if restored == 0:
            self.stdout.write("No inbox numbers need restoring.")
        elif apply:
            self.stdout.write(self.style.SUCCESS(f"Restored {restored} inbox number(s)."))
        else:
            self.stdout.write(f"Dry run. {restored} inbox number(s) would be restored. Pass --apply.")
