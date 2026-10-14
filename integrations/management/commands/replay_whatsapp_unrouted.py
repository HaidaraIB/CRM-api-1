"""
Replay inbound WhatsApp messages that were held because the number was not connected.

  python manage.py replay_whatsapp_unrouted                         # dry run
  python manage.py replay_whatsapp_unrouted --apply
  python manage.py replay_whatsapp_unrouted --phone-number-id ID --apply
  python manage.py replay_whatsapp_unrouted --from-log logs/django.log --apply
  python manage.py replay_whatsapp_unrouted --purge-older-than-days 30 --apply

``--from-log`` reads DEBUG lines ``WhatsApp webhook payload:`` (only present when
DJANGO_LOG_FILE_LEVEL=DEBUG). Messages for a number that is connected now are
stored immediately; the rest are held and delivered by ``--apply``.
"""
from django.core.management.base import BaseCommand, CommandError

from integrations.models import WhatsAppUnroutedMessage
from integrations.services.whatsapp_unrouted import (
    import_webhook_payload,
    iter_webhook_payloads_from_log,
    purge_unrouted,
    replay_unrouted,
)


class Command(BaseCommand):
    help = "Replay held inbound WhatsApp messages (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Default is a dry run.")
        parser.add_argument("--phone-number-id", default="", help="Only this Meta phone_number_id.")
        parser.add_argument(
            "--from-log",
            default="",
            help="Import DEBUG webhook payloads from a django log file.",
        )
        parser.add_argument(
            "--purge-older-than-days",
            type=int,
            default=None,
            help="Delete held rows older than this many days.",
        )

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        phone_number_id = (options["phone_number_id"] or "").strip()
        log_path = (options["from_log"] or "").strip()
        purge_days = options["purge_older_than_days"]

        if log_path:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError as exc:
                raise CommandError(f"Could not read {log_path}: {exc}") from exc
            payloads = list(iter_webhook_payloads_from_log(text))
            imported = 0
            for payload in payloads:
                imported += import_webhook_payload(payload, apply=apply)
            self.stdout.write(
                f"{'Imported' if apply else 'Would import'} {imported} inbound message(s) "
                f"from {len(payloads)} payload(s) in {log_path}."
            )

        pending = WhatsAppUnroutedMessage.objects.filter(replayed_at__isnull=True)
        if phone_number_id:
            pending = pending.filter(phone_number_id=phone_number_id)
        self.stdout.write(f"Pending held messages: {pending.count()}.")
        if apply:
            result = replay_unrouted(phone_number_id or None)
            self.stdout.write(
                "Replayed {replayed}, still held {still_held}, failed {failed}.".format(**result)
            )
        else:
            self.stdout.write("Dry run. Pass --apply to deliver pending messages.")

        if purge_days is not None:
            if purge_days < 1:
                raise CommandError("--purge-older-than-days must be at least 1.")
            if apply:
                deleted = purge_unrouted(purge_days)
                self.stdout.write(f"Purged {deleted} held message row(s) older than {purge_days} days.")
            else:
                self.stdout.write(
                    f"Dry run. Pass --apply to purge rows older than {purge_days} days."
                )
