"""
Daily phone-system usage (PbxUsageDaily). Run hourly from cron:

    python manage.py rollup_pbx_usage [--days N]

Recomputes the last N days (default 2: today and yesterday) for every company
with telephony; re-running is safe.
"""

from django.core.management.base import BaseCommand

from telephony.services.usage import rollup


class Command(BaseCommand):
    help = "Recompute daily phone-system usage per company."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=2)

    def handle(self, *args, **opts):
        written = rollup(days=max(1, opts["days"]))
        self.stdout.write(self.style.SUCCESS(f"{written} company-day row(s) written"))
