"""
Assign the company default lead status to clients that have status=NULL.

One-time / maintenance backfill for integration leads (Meta, TikTok, WhatsApp, etc.)
created before default status was applied on create.

Usage:
    python manage.py backfill_lead_default_status
    python manage.py backfill_lead_default_status --dry-run
    python manage.py backfill_lead_default_status --company-id 1
"""

from django.core.management.base import BaseCommand
from django.db.models import F

from companies.models import Company
from crm.lead_defaults import get_default_lead_status
from crm.models import Client


class Command(BaseCommand):
    help = "Set default lead status on clients with no status (status_id IS NULL)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Limit to one company ID.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without updating.",
        )

    def handle(self, *args, **options):
        company_id = options["company_id"]
        dry_run = options["dry_run"]

        base_qs = Client.objects.filter(status_id__isnull=True)
        if company_id is not None:
            base_qs = base_qs.filter(company_id=company_id)

        total_candidates = base_qs.count()
        if total_candidates == 0:
            self.stdout.write(self.style.SUCCESS("No clients with missing status."))
            return

        company_ids = list(
            base_qs.values_list("company_id", flat=True).distinct().order_by("company_id")
        )

        updated_total = 0
        skipped_companies = 0

        for cid in company_ids:
            try:
                company = Company.objects.get(pk=cid)
            except Company.DoesNotExist:
                count = base_qs.filter(company_id=cid).count()
                self.stdout.write(
                    self.style.WARNING(
                        f"Company id={cid} not found; skipping {count} client(s)."
                    )
                )
                skipped_companies += 1
                continue

            default_status = get_default_lead_status(company)
            if not default_status:
                count = base_qs.filter(company_id=cid).count()
                self.stdout.write(
                    self.style.WARNING(
                        f'Company "{company.name}" (id={cid}): no default lead status; '
                        f"skipping {count} client(s)."
                    )
                )
                skipped_companies += 1
                continue

            company_qs = base_qs.filter(company_id=cid)
            count = company_qs.count()

            if dry_run:
                self.stdout.write(
                    f"[DRY RUN] company={cid} ({company.name!r}): would set status "
                    f'"{default_status.name}" (id={default_status.id}) on {count} client(s).'
                )
            else:
                updated = company_qs.update(
                    status_id=default_status.id,
                    status_entered_at=F("created_at"),
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f'company={cid} ({company.name!r}): set status '
                        f'"{default_status.name}" on {updated} client(s).'
                    )
                )
                updated_total += updated

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n[DRY RUN] {total_candidates} client(s) across "
                    f"{len(company_ids)} company/companies would be updated."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nUpdated {updated_total} client(s); "
                    f"{skipped_companies} company/companies skipped (no default status)."
                )
            )
