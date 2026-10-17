from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from companies.models import Company
from reports.backfill import rebuild_company


class Command(BaseCommand):
    help = (
        "Rebuild backfilled report history (status transitions, assignment periods, first responses, "
        "conversation replies) from legacy ClientEvent rows. Idempotent; live-recorded rows are never touched."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, help="Only this company id")
        parser.add_argument("--since", help="Only leads created on/after this date (YYYY-MM-DD)")

    def handle(self, *args, **opts):
        since = None
        if opts.get("since"):
            try:
                since = timezone.make_aware(datetime.strptime(opts["since"], "%Y-%m-%d"))
            except ValueError as exc:
                raise CommandError("--since must be YYYY-MM-DD") from exc
        companies = Company.objects.all()
        if opts.get("company"):
            companies = companies.filter(pk=opts["company"])
        for company in companies:
            totals = rebuild_company(company, since=since, log=self.stdout.write)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{company.name}: {totals['leads']} leads -> {totals['transitions']} transitions, "
                    f"{totals['periods']} assignment periods, {totals['replies']} replies"
                )
            )
