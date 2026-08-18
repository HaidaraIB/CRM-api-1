"""
Escalate unacknowledged walk-in arrivals to the company owner and manage_leads
supervisors once `escalation_due_at` has passed.

Usage:
    python manage.py check_lead_arrival_escalations
    python manage.py check_lead_arrival_escalations --dry-run

Intended cadence: every minute (a 5-minute SLA needs finer-grained polling than the
15-minute cadence used by the daily-digest-style commands in this app).
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.arrivals import arrival_escalation_recipients
from crm.models import LeadArrival
from notifications.dispatch import claim_dispatch, mark_dispatched
from notifications.models import NotificationType
from notifications.services import NotificationService

logger = logging.getLogger(__name__)

# After a cron outage, do not resurrect escalations for arrivals from hours/days ago.
BACKLOG_GUARD_HOURS = 6
MAX_RECIPIENTS_PER_ARRIVAL = 10


class Command(BaseCommand):
    help = "Escalate unacknowledged walk-in arrivals past their escalation_due_at."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be escalated without sending or claiming dispatch.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        now = timezone.now()

        arrivals = (
            LeadArrival.objects.filter(
                acknowledged_at__isnull=True,
                escalated_at__isnull=True,
                escalation_due_at__isnull=False,
                escalation_due_at__lte=now,
                announced_at__gte=now - timedelta(hours=BACKLOG_GUARD_HOURS),
            )
            .select_related("company", "company__owner", "client")
            .prefetch_related("notified_users")
        )

        if not arrivals.exists():
            self.stdout.write(self.style.SUCCESS("No arrivals due for escalation."))
            return

        escalated_count = 0
        notified_count = 0

        for arrival in arrivals:
            already_notified_ids = {u.id for u in arrival.notified_users.all()}
            recipients = arrival_escalation_recipients(
                arrival.company, exclude_user_ids=already_notified_ids
            )[:MAX_RECIPIENTS_PER_ARRIVAL]

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[DRY RUN] Would escalate arrival {arrival.id} "
                        f"({arrival.client.name}) to {len(recipients)} recipient(s)"
                    )
                )
                continue

            for recipient in recipients:
                log_row = claim_dispatch(
                    user=recipient,
                    notification_type=NotificationType.CUSTOMER_ARRIVAL_ESCALATED,
                    obj=arrival,
                    scheduled_for=arrival.escalation_due_at,
                    minutes_before=0,
                    dedupe_key="arrival_escalation",
                    expect_email=False,
                )
                if log_row is None:
                    continue
                try:
                    NotificationService.send_notification(
                        user=recipient,
                        notification_type=NotificationType.CUSTOMER_ARRIVAL_ESCALATED,
                        data={
                            "kind": "lead_arrival",
                            "arrival_id": arrival.id,
                            "lead_id": arrival.client_id,
                            "lead_name": arrival.client.name,
                            "client_id": str(arrival.client_id),
                            "routing": arrival.routing,
                            "invalidate": "crm:arrivals",
                        },
                        skip_settings_check=True,
                    )
                    mark_dispatched(log_row, push_sent=True)
                    notified_count += 1
                except Exception as e:
                    logger.error(
                        "Error escalating arrival %s to %s: %s", arrival.id, recipient.username, e
                    )
                    mark_dispatched(log_row, push_sent=False, error=str(e))

            updated = LeadArrival.objects.filter(
                pk=arrival.pk, escalated_at__isnull=True
            ).update(escalated_at=now)
            if updated:
                escalated_count += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"[DRY RUN] {arrivals.count()} arrival(s) would escalate."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Escalated {escalated_count} arrival(s), sent {notified_count} notification(s)."
                )
            )
