"""
Alert on leads that have gone without follow-up, on a per-lead clock.

Each lead is evaluated against its own reference time (last contact, else assignment, else
creation) plus the company's configured SLA (`Company.no_follow_up_hours`, default 10h).
The command is therefore run frequently (every 15 minutes) and notifies each lead exactly
when *that lead* becomes overdue — instead of the old fixed 6-hourly sweep, which fired a
burst of simultaneous pushes for every overdue lead at once.

Escalation: a lead that stays untouched is flagged at 1x, 2x and 3x the SLA and then goes
quiet until it is genuinely contacted again (which moves the reference time and re-arms the
ladder automatically). Each rung is claimed in ReminderDispatchLog, so re-running the
command never repeats a notification.

The assignee gets the per-lead `lead_no_follow_up` alert. The company owner gets a single
daily `team_activity` digest at `Company.no_follow_up_digest_hour` in the company's own
timezone, rather than one push per overdue lead.

Usage:
    python manage.py check_lead_no_follow_up
    python manage.py check_lead_no_follow_up --hours 10     # override the per-company SLA
    python manage.py check_lead_no_follow_up --dry-run
"""
import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Client
from notifications.dispatch import (
    claim_dispatch,
    due_local_slot,
    escalation_step,
    mark_dispatched,
)
from notifications.models import NotificationType
from notifications.services import NotificationService
from notifications.team_activity import notify_owner_team_activity
from settings.models import StatusCategory
from subscriptions.entitlements import get_active_subscription

# A lead is flagged at 1x, 2x and 3x the SLA, then goes quiet until contacted again.
MAX_ESCALATIONS = 3

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Notify assignees about leads overdue for follow-up (per-lead SLA; run every 15 minutes)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=None,
            help='Override every company\'s configured SLA (default: use Company.no_follow_up_hours)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending',
        )

    def _echo(self, message):
        """
        Write progress without letting the console kill the run.

        Lead and employee names are frequently Arabic, and a terminal on a narrow codepage
        (e.g. Windows cp1252) raises UnicodeEncodeError on write. Reporting is not worth
        aborting a notification sweep for.
        """
        try:
            self.stdout.write(message)
        except UnicodeEncodeError:
            self.stdout.write(message.encode('ascii', 'replace').decode('ascii'))

    def handle(self, *args, **options):
        hours_override = options.get('hours')
        dry_run = options.get('dry_run', False)
        now = timezone.now()

        sent_count = 0
        skipped_count = 0
        # company_id -> {"company": Company, "leads": int, "employees": set()}
        overdue_by_company = defaultdict(lambda: {"company": None, "leads": 0, "employees": set()})
        # Resolved once per company rather than once per lead.
        subscription_ok = {}

        leads = (
            Client.objects.filter(
                assigned_to__isnull=False,
                company__isnull=False,
                company__no_follow_up_enabled=True,
            )
            .exclude(
                status__category__in=[
                    StatusCategory.CLOSED.value,
                    StatusCategory.INACTIVE.value,
                ]
            )
            .select_related('assigned_to', 'company', 'company__owner', 'status')
        )

        for lead in leads.iterator(chunk_size=500):
            company = lead.company

            if company.id not in subscription_ok:
                subscription_ok[company.id] = get_active_subscription(company) is not None
            if not subscription_ok[company.id]:
                skipped_count += 1
                continue

            sla_hours = hours_override or company.no_follow_up_hours or 10
            reference = lead.last_contacted_at or lead.assigned_at or lead.created_at

            due = escalation_step(reference, sla_hours, MAX_ESCALATIONS, now=now)
            if due is None:
                skipped_count += 1
                continue
            step, due_at, overdue_hours = due

            # Count toward the owner digest whenever the lead is currently overdue,
            # independently of whether this particular rung still needs sending.
            bucket = overdue_by_company[company.id]
            bucket["company"] = company
            bucket["leads"] += 1
            bucket["employees"].add(lead.assigned_to_id)

            if dry_run:
                self._echo(
                    self.style.SUCCESS(
                        f'[DRY RUN] Lead {lead.id} ({lead.name}) -> {lead.assigned_to.username}: '
                        f'step {step}/{MAX_ESCALATIONS}, {overdue_hours}h overdue, due at {due_at.isoformat()}'
                    )
                )
                sent_count += 1
                continue

            log_row = claim_dispatch(
                user=lead.assigned_to,
                notification_type=NotificationType.LEAD_NO_FOLLOW_UP,
                obj=lead,
                scheduled_for=due_at,
            )
            if log_row is None:
                skipped_count += 1
                continue

            try:
                NotificationService.send_notification(
                    user=lead.assigned_to,
                    notification_type=NotificationType.LEAD_NO_FOLLOW_UP,
                    data={
                        'lead_id': lead.id,
                        'lead_name': lead.name,
                        'hours': overdue_hours,
                        'escalation_step': step,
                    },
                    lead_source=getattr(lead, 'source', None),
                )
                mark_dispatched(log_row, push_sent=True)
                sent_count += 1
            except Exception as e:
                logger.error("Error sending no-follow-up notification for lead %s: %s", lead.id, e)
                self._echo(self.style.ERROR(f'Error sending notification for lead {lead.id}: {e}'))
                mark_dispatched(log_row, error=str(e))
                skipped_count += 1
                continue

            # Reported outside the try: a console encoding failure must never be
            # recorded as a failed send.
            self._echo(
                self.style.SUCCESS(
                    f'Sent no-follow-up alert to {lead.assigned_to.username} for lead '
                    f'{lead.id} ({lead.name}) - {overdue_hours}h overdue (step {step})'
                )
            )

        digests = self._send_owner_digests(overdue_by_company, dry_run=dry_run)

        prefix = '[DRY RUN] Would send' if dry_run else 'Sent'
        self._echo(
            self.style.SUCCESS(
                f'\n{prefix} {sent_count} alert(s) and {digests} owner digest(s), skipped {skipped_count}'
            )
        )

    def _send_owner_digests(self, overdue_by_company, *, dry_run):
        """
        One digest per company per local day, at the company's configured local hour.

        Companies with no overdue leads are skipped entirely — silence is the correct
        signal, and it keeps quiet tenants out of the owner's inbox.
        """
        sent = 0

        for entry in overdue_by_company.values():
            company = entry["company"]
            if company is None or not company.owner_id:
                continue

            digest_hour = company.no_follow_up_digest_hour
            if digest_hour is None:
                digest_hour = 9

            slot = due_local_slot(company, digest_hour)
            if slot is None:
                continue

            lead_count = entry["leads"]
            employee_count = len(entry["employees"])

            if dry_run:
                self._echo(
                    self.style.WARNING(
                        f'[DRY RUN] Would send owner digest for company {company.id} ({company.name}): '
                        f'{lead_count} lead(s) across {employee_count} employee(s)'
                    )
                )
                sent += 1
                continue

            log_row = claim_dispatch(
                user=company.owner,
                notification_type=NotificationType.TEAM_ACTIVITY,
                obj=company,
                scheduled_for=slot,
                dedupe_key="no_follow_up_digest",
            )
            if log_row is None:
                continue

            try:
                notify_owner_team_activity(
                    None,
                    company,
                    action="no_follow_up_digest",
                    count=lead_count,
                    employee_count=employee_count,
                )
                mark_dispatched(log_row, push_sent=True)
                sent += 1
                self._echo(
                    self.style.SUCCESS(
                        f'Sent owner digest for company {company.id} ({company.name}): '
                        f'{lead_count} lead(s), {employee_count} employee(s)'
                    )
                )
            except Exception as e:
                logger.error("Error sending no-follow-up digest for company %s: %s", company.id, e)
                mark_dispatched(log_row, error=str(e))

        return sent
