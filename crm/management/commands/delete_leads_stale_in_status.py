"""
Delete leads that have remained in a LeadStatus longer than that status's
auto_delete_after_hours (when configured). Run from cron (e.g. hourly).

Usage:
    python manage.py delete_leads_stale_in_status
    python manage.py delete_leads_stale_in_status --dry-run
    python manage.py delete_leads_stale_in_status --company-id 1
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.models import Client
from settings.models import LeadStatus

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Hard-delete clients stuck in a status past auto_delete_after_hours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Log candidates without deleting.",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Limit to one company ID.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        company_id = options["company_id"]
        now = timezone.now()

        qs = LeadStatus.objects.filter(
            auto_delete_after_hours__isnull=False,
            is_active=True,
        ).select_related("company")
        if company_id is not None:
            qs = qs.filter(company_id=company_id)

        statuses = list(qs)
        if not statuses:
            self.stdout.write(self.style.SUCCESS("No lead statuses with auto_delete_after_hours set."))
            return

        total_deleted_clients = 0
        for status in statuses:
            hours = status.auto_delete_after_hours
            if not hours or hours < 1:
                continue
            threshold = now - timedelta(hours=hours)

            if dry_run:
                count = Client.objects.filter(
                    company_id=status.company_id,
                    status_id=status.id,
                    status_entered_at__lte=threshold,
                ).count()
                sample = list(
                    Client.objects.filter(
                        company_id=status.company_id,
                        status_id=status.id,
                        status_entered_at__lte=threshold,
                    ).values_list("pk", flat=True)[:10]
                )
                self.stdout.write(
                    f"[DRY RUN] company={status.company_id} status={status.id} ({status.name!r}) "
                    f"hours={hours} candidates={count} sample_ids={list(sample)}"
                )
                logger.info(
                    "delete_leads_stale_in_status dry-run company=%s status=%s count=%s sample=%s",
                    status.company_id,
                    status.id,
                    count,
                    list(sample),
                )
                continue

            deleted_here = 0
            while True:
                chunk = list(
                    Client.objects.filter(
                        company_id=status.company_id,
                        status_id=status.id,
                        status_entered_at__lte=threshold,
                    ).values_list("pk", flat=True)[:BATCH_SIZE]
                )
                if not chunk:
                    break
                with transaction.atomic():
                    _, details = Client.objects.filter(pk__in=chunk).delete()
                    deleted_here += details.get("crm.Client", 0)
            if deleted_here:
                total_deleted_clients += deleted_here
                self.stdout.write(
                    self.style.WARNING(
                        f"Deleted {deleted_here} lead(s) for status {status.id} ({status.name}) "
                        f"company={status.company_id}"
                    )
                )
                logger.warning(
                    "delete_leads_stale_in_status deleted_clients=%s company=%s status=%s",
                    deleted_here,
                    status.company_id,
                    status.id,
                )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete; no rows deleted."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Done. Leads (Client rows) deleted: {total_deleted_clients}")
            )
