"""
Call-recording retention (Loop Cloud PBX, contract section 7). Run daily from cron:

    python manage.py purge_pbx_recordings [--dry-run]

Deletes each tenant's recordings older than its ``recording_retention_days``
(object first, then the row; a storage error keeps the row for the next run),
skipping tenants on legal hold, then re-checks the recording storage quota.
"""

from django.core.management.base import BaseCommand

from telephony.services.recordings import purge_expired


class Command(BaseCommand):
    help = "Delete PBX call recordings past each company's retention period."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be deleted; delete nothing.")
        parser.add_argument("--batch", type=int, default=500, help="At most this many recordings per company per run.")

    def handle(self, *args, **opts):
        report = purge_expired(dry_run=opts["dry_run"], batch=opts["batch"])
        verb = "would delete" if opts["dry_run"] else "deleted"
        for company_id, stats in sorted(report.items()):
            self.stdout.write(f"company {company_id}: {verb} {stats['deleted']} ({stats['bytes']} bytes), "
                              f"{stats['failed']} failed")
        self.stdout.write(self.style.SUCCESS(f"done: {len(report)} company(ies)"))
