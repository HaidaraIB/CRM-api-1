"""
Delete stored Instagram/Messenger attachments past their retention window.

META_INBOX_MAX_MEDIA_BYTES bounds one message, not a tenant. Inbox media is
charged against no plan quota, and an IG thread carries far more of it than a
WhatsApp one, so without this the media directory grows for as long as the tenant
keeps receiving DMs.

Message rows are kept. Only the stored bytes go, so the thread still renders the
bubble, its caption and its kind — the attachment just stops being downloadable.
This is why the retention default is generous: it is a storage bound, not a
privacy control.

Usage:
    python manage.py purge_social_media
    python manage.py purge_social_media --days 30 --dry-run
    python manage.py purge_social_media --company-id 12
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations.models import SocialMessage

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def _human_bytes(total: int) -> str:
    value = float(total)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class Command(BaseCommand):
    help = "Delete social inbox attachments older than the retention window"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Retention window in days "
                "(default: settings.META_INBOX_MEDIA_RETENTION_DAYS)"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without touching storage",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Limit the purge to one company",
        )

    def handle(self, *args, **options):
        days = options.get("days")
        if days is None:
            days = int(getattr(settings, "META_INBOX_MEDIA_RETENTION_DAYS", 90))
        if days < 1:
            raise CommandError("--days must be at least 1")

        dry_run = options.get("dry_run", False)
        company_id = options.get("company_id")
        cutoff = timezone.now() - timedelta(days=days)

        stale = (
            SocialMessage.objects.filter(created_at__lt=cutoff)
            .exclude(attachment="")
            .exclude(attachment__isnull=True)
            .select_related("conversation")
            .order_by("pk")
        )
        if company_id:
            stale = stale.filter(conversation__company_id=company_id)

        if dry_run:
            count = stale.count()
            # attachment_size is the recorded size; a null one is an older row
            # whose size was never captured, so this is a floor, not a total.
            known = sum(
                size
                for size in stale.values_list("attachment_size", flat=True)
                if size
            )
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would purge {count} attachment(s) older than "
                    f"{cutoff.isoformat()} (at least {_human_bytes(known)})"
                )
            )
            return

        purged = 0
        failed = 0
        freed = 0
        last_pk = 0

        while True:
            batch = list(stale.filter(pk__gt=last_pk)[:BATCH_SIZE])
            if not batch:
                break
            last_pk = batch[-1].pk

            for message in batch:
                size = message.attachment_size or 0
                try:
                    # delete() removes the bytes from storage and clears the field.
                    message.attachment.delete(save=False)
                except Exception as exc:
                    # A file already gone from disk still leaves a row pointing at
                    # it, and that row is the reason this query keeps finding it.
                    # Clear the pointer either way.
                    logger.warning(
                        "purge_social_media: could not delete file for message %s: %s",
                        message.pk,
                        exc,
                    )
                    message.attachment = None
                    failed += 1

                message.attachment_size = None
                message.attachment_object_key = ""
                # Per-instance save, so the post_save receiver in sync/signals.py
                # bumps the `inbox` slice for us — a bulk update() here would fire
                # no signal and clients would 304 with a dead attachment URL.
                message.save(
                    update_fields=[
                        "attachment",
                        "attachment_size",
                        "attachment_object_key",
                    ]
                )
                purged += 1
                freed += size

        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {purged} attachment(s) older than {cutoff.isoformat()}, "
                f"freeing at least {_human_bytes(freed)}"
                + (f" ({failed} file(s) already missing)" if failed else "")
            )
        )
