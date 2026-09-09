"""
Daily health sweep over connected Meta Inbox Pages.

Meta keeps accepting sends on a Page whose app subscription was removed in
Business Settings while quietly delivering no webhooks, so a broken inbox is
indistinguishable from a quiet one. `check_connection_health` already detects it,
but until this command existed nothing ran it on a schedule — only an admin
opening the Integrations page did, which is exactly who would never think to look.

Usage:
    python manage.py check_meta_inbox_connections
    python manage.py check_meta_inbox_connections --dry-run
    python manage.py check_meta_inbox_connections --company-id 12
"""

from django.core.management.base import BaseCommand

from integrations.models import MetaInboxConnection
from integrations.services.meta_inbox_connections import (
    check_connection_health,
    notify_owner_inbox_connection_broken,
)

# 'disconnected' is excluded on purpose: the tenant, or a plan/policy change,
# turned those off deliberately. 'error' rows stay in scope so a Page that comes
# back recovers on its own instead of needing a manual re-check.
CHECKABLE_STATUSES = ('connected', 'error')


class Command(BaseCommand):
    help = "Verify every connected Meta Inbox Page is still subscribed to the app"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many connections would be checked, without calling Graph",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Limit the sweep to one company",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        company_id = options.get("company_id")

        connections = MetaInboxConnection.objects.filter(
            status__in=CHECKABLE_STATUSES
        ).select_related('company', 'company__owner')
        if company_id:
            connections = connections.filter(company_id=company_id)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would check {connections.count()} connection(s)."
                )
            )
            return

        healthy = recovered = broken = notified = unknown = 0

        for connection in connections:
            previous_status = connection.status
            result = check_connection_health(connection)

            if result.get('ok'):
                healthy += 1
                if previous_status == 'error':
                    recovered += 1
                continue

            # A Graph error means we did not get an answer. Treating that as a
            # broken Page would email every owner during a Meta outage, and
            # check_connection_health deliberately writes nothing in that case.
            if result.get('error_key') == 'meta_inbox_health_check_failed':
                unknown += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Page {connection.page_id}: health check inconclusive "
                        f"({result.get('error')})"
                    )
                )
                continue

            broken += 1
            self.stdout.write(
                self.style.ERROR(
                    f"Page {connection.page_id} (company {connection.company_id}) "
                    f"is no longer receiving messages"
                )
            )
            # Only on the transition. The 'error' status persists between runs, so
            # notifying on state rather than on change would re-alert the owner
            # every single day until they got around to reconnecting.
            if previous_status == 'connected' and notify_owner_inbox_connection_broken(
                connection
            ):
                notified += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {healthy + broken + unknown} connection(s): "
                f"{healthy} healthy ({recovered} recovered), {broken} broken "
                f"({notified} owner(s) notified), {unknown} inconclusive"
            )
        )
