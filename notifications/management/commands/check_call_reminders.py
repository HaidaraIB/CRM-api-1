"""
Management command to check for call reminders and send notifications
Usage:
    python manage.py check_call_reminders
    python manage.py check_call_reminders --minutes-before 15
    python manage.py check_call_reminders --window-minutes 15
    python manage.py check_call_reminders --dry-run
"""
from datetime import timedelta
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.event_emails import send_followup_reminder_email
from accounts.models import Role, User
from crm.models import ClientCall, ClientFieldVisit, ClientVisit
from notifications.dispatch import claim_dispatch
from notifications.models import NotificationType
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


def _process_upcoming_visit_reminders(
    *,
    visits,
    assignee_notification_type,
    reception_notification_type,
    reminder_kind_assignee,
    reminder_kind_reception,
    assignee_email_kind,
    reception_email_kind,
    assignee_email_title,
    reception_email_title,
    visit_id_data_key,
    visit_label,
    dry_run,
    minutes_before,
    now,
    stdout,
    style,
):
    """Shared logic for office visits and field visits (upcoming_visit_date)."""
    sent_count = 0
    skipped_count = 0
    dedup_skipped = 0

    for visit in visits:
        lead = visit.client
        scheduled_for = visit.upcoming_visit_date

        if lead.assigned_to:
            user = lead.assigned_to

            if dry_run:
                # Claiming would write to the dispatch log, so never claim during a dry run.
                stdout.write(
                    style.SUCCESS(
                        f"[DRY RUN] Would send reminder to {user.username} "
                        f"for {visit_label} {visit.id} - Reminder at "
                        f"{visit.upcoming_visit_date}"
                    )
                )
                sent_count += 1
            else:
                log_row = claim_dispatch(
                    user=user,
                    notification_type=assignee_notification_type,
                    obj=visit,
                    scheduled_for=scheduled_for,
                    minutes_before=minutes_before,
                    expect_email=True,
                )
                if log_row is None:
                    dedup_skipped += 1
                else:
                    try:
                        minutes_remaining = int(
                            (visit.upcoming_visit_date - now).total_seconds() / 60
                        )
                        user_lang = getattr(user, "language", "ar") or "ar"

                        NotificationService.send_notification(
                            user=user,
                            notification_type=assignee_notification_type,
                            data={
                                visit_id_data_key: visit.id,
                                "lead_id": lead.id,
                                "lead_name": lead.name,
                                "minutes_remaining": minutes_remaining,
                                "minutes_before": minutes_before,
                                "reminder_kind": reminder_kind_assignee,
                                "reminder_time": (
                                    scheduled_for.isoformat() if scheduled_for else None
                                ),
                            },
                            lead_source=getattr(lead, "source", None),
                        )
                        log_row.push_sent = True

                        email_ok = send_followup_reminder_email(
                            user,
                            reminder_kind=assignee_email_kind,
                            title=assignee_email_title.format(lead_name=lead.name),
                            lead_name=lead.name,
                            scheduled_for=scheduled_for,
                            minutes_before=minutes_before,
                            language=user_lang,
                        )
                        if email_ok:
                            log_row.email_sent = True
                        sent_count += 1
                        stdout.write(
                            style.SUCCESS(
                                f"Sent reminder to {user.username} for "
                                f"{visit_label} {visit.id}"
                            )
                        )
                    except Exception as e:
                        logger.error(
                            "Error sending reminder for %s %s: %s",
                            visit_label,
                            visit.id,
                            e,
                        )
                        stdout.write(
                            style.ERROR(
                                f"Error sending reminder for {visit_label} "
                                f"{visit.id}: {e}"
                            )
                        )
                        log_row.last_error = str(e)
                        skipped_count += 1
                    finally:
                        log_row.save(
                            update_fields=[
                                "push_sent",
                                "email_sent",
                                "last_error",
                                "updated_at",
                            ]
                        )
        elif getattr(lead.company, "specialization", None) != "medical":
            skipped_count += 1

        company = getattr(lead, "company", None)
        if company and getattr(company, "specialization", None) == "medical":
            for rec_user in User.objects.filter(
                company=company,
                role=Role.RECEPTION.value,
                is_active=True,
            ):
                if dry_run:
                    # Claiming would write to the dispatch log, so never claim during a dry run.
                    stdout.write(
                        style.SUCCESS(
                            f"[DRY RUN] Would send reception {visit_label} reminder to "
                            f"{rec_user.username} for {visit_label} {visit.id}"
                        )
                    )
                    sent_count += 1
                    continue

                rlog = claim_dispatch(
                    user=rec_user,
                    notification_type=reception_notification_type,
                    obj=visit,
                    scheduled_for=scheduled_for,
                    minutes_before=minutes_before,
                    expect_email=True,
                )
                if rlog is None:
                    dedup_skipped += 1
                    continue

                try:
                    minutes_remaining = int(
                        (visit.upcoming_visit_date - now).total_seconds() / 60
                    )
                    user_lang = getattr(rec_user, "language", "ar") or "ar"
                    NotificationService.send_notification(
                        user=rec_user,
                        notification_type=reception_notification_type,
                        data={
                            visit_id_data_key: visit.id,
                            "lead_id": lead.id,
                            "lead_name": lead.name,
                            "minutes_remaining": minutes_remaining,
                            "minutes_before": minutes_before,
                            "reminder_kind": reminder_kind_reception,
                            "reminder_time": (
                                scheduled_for.isoformat() if scheduled_for else None
                            ),
                        },
                        lead_source=getattr(lead, "source", None),
                    )
                    rlog.push_sent = True
                    email_ok = send_followup_reminder_email(
                        rec_user,
                        reminder_kind=reception_email_kind,
                        title=reception_email_title.format(lead_name=lead.name),
                        lead_name=lead.name,
                        scheduled_for=scheduled_for,
                        minutes_before=minutes_before,
                        language=user_lang,
                    )
                    if email_ok:
                        rlog.email_sent = True
                    sent_count += 1
                    stdout.write(
                        style.SUCCESS(
                            f"Sent reception {visit_label} reminder to "
                            f"{rec_user.username} for {visit_label} {visit.id}"
                        )
                    )
                except Exception as e:
                    logger.error(
                        "Error sending reception %s reminder for %s %s: %s",
                        visit_label,
                        visit_label,
                        visit.id,
                        e,
                    )
                    stdout.write(
                        style.ERROR(
                            f"Error sending reception {visit_label} reminder for "
                            f"{visit_label} {visit.id}: {e}"
                        )
                    )
                    rlog.last_error = str(e)
                    skipped_count += 1
                finally:
                    rlog.save(
                        update_fields=[
                            "push_sent",
                            "email_sent",
                            "last_error",
                            "updated_at",
                        ]
                    )

    return sent_count, skipped_count, dedup_skipped


class Command(BaseCommand):
    help = "Check for call / visit / field visit reminders and send notifications"

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes-before",
            type=int,
            default=15,
            help="Number of minutes before reminder time to send notification (default: 15)",
        )
        parser.add_argument(
            "--window-minutes",
            type=int,
            default=15,
            help=(
                "Window size in minutes starting at (now + minutes-before). "
                "Default: 15. Should be >= cron interval to avoid missing reminders."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without actually sending",
        )

    def handle(self, *args, **options):
        minutes_before = options.get("minutes_before", 15)
        window_minutes = options.get("window_minutes", 15)
        dry_run = options.get("dry_run", False)

        now = timezone.now()
        reminder_start = now + timedelta(minutes=minutes_before)
        reminder_end = reminder_start + timedelta(minutes=window_minutes)

        # Already-completed follow-ups need no reminder — complete_open_contact_items_for_client
        # bulk-sets follow_up_completed_at when a later call is logged.
        calls = ClientCall.objects.filter(
            follow_up_date__gte=reminder_start,
            follow_up_date__lt=reminder_end,
            follow_up_completed_at__isnull=True,
            client__assigned_to__isnull=False,
        ).select_related("client", "client__assigned_to")

        visits = ClientVisit.objects.filter(
            upcoming_visit_date__gte=reminder_start,
            upcoming_visit_date__lt=reminder_end,
        ).select_related("client", "client__assigned_to", "client__company")

        field_visits = ClientFieldVisit.objects.filter(
            upcoming_visit_date__gte=reminder_start,
            upcoming_visit_date__lt=reminder_end,
        ).select_related("client", "client__assigned_to", "client__company")

        if (
            not calls.exists()
            and not visits.exists()
            and not field_visits.exists()
        ):
            self.stdout.write(
                self.style.SUCCESS(
                    f"No call/visit/field visit reminders found in the next "
                    f"{minutes_before} minutes."
                )
            )
            return

        sent_count = 0
        skipped_count = 0
        dedup_skipped = 0

        for call in calls:
            lead = call.client
            if not lead.assigned_to:
                skipped_count += 1
                continue
            user = lead.assigned_to
            scheduled_for = call.follow_up_date

            if dry_run:
                # Claiming would write to the dispatch log, so never claim during a dry run.
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[DRY RUN] Would send reminder to {user.username} "
                        f"for call {call.id} - Reminder at {call.follow_up_date}"
                    )
                )
                sent_count += 1
                continue

            log_row = claim_dispatch(
                user=user,
                notification_type=NotificationType.CALL_REMINDER,
                obj=call,
                scheduled_for=scheduled_for,
                minutes_before=minutes_before,
                expect_email=True,
            )
            if log_row is None:
                dedup_skipped += 1
                continue

            try:
                minutes_remaining = int(
                    (call.follow_up_date - now).total_seconds() / 60
                )
                NotificationService.send_notification(
                    user=user,
                    notification_type=NotificationType.CALL_REMINDER,
                    data={
                        "call_id": call.id,
                        "lead_id": lead.id,
                        "lead_name": lead.name,
                        "minutes_remaining": minutes_remaining,
                        "minutes_before": minutes_before,
                    },
                    lead_source=getattr(lead, "source", None),
                )
                log_row.push_sent = True

                email_ok = send_followup_reminder_email(
                    user,
                    reminder_kind="call",
                    title=f"Call follow-up: {lead.name}",
                    lead_name=lead.name,
                    scheduled_for=scheduled_for,
                    minutes_before=minutes_before,
                    language=getattr(user, "language", "ar") or "ar",
                )
                if email_ok:
                    log_row.email_sent = True
                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sent reminder to {user.username} for call {call.id}"
                    )
                )
            except Exception as e:
                logger.error("Error sending reminder for call %s: %s", call.id, e)
                self.stdout.write(
                    self.style.ERROR(
                        f"Error sending reminder for call {call.id}: {e}"
                    )
                )
                log_row.last_error = str(e)
                skipped_count += 1
            finally:
                log_row.save(
                    update_fields=[
                        "push_sent",
                        "email_sent",
                        "last_error",
                        "updated_at",
                    ]
                )

        visit_kwargs = dict(
            dry_run=dry_run,
            minutes_before=minutes_before,
            now=now,
            stdout=self.stdout,
            style=self.style,
        )

        v_sent, v_skip, v_dedup = _process_upcoming_visit_reminders(
            visits=visits,
            assignee_notification_type=NotificationType.VISIT_REMINDER,
            reception_notification_type=NotificationType.RECEPTION_VISIT_REMINDER,
            reminder_kind_assignee="visit",
            reminder_kind_reception="reception_visit",
            assignee_email_kind="visit",
            reception_email_kind="visit_reception",
            assignee_email_title="Visit follow-up: {lead_name}",
            reception_email_title="Patient appointment reminder: {lead_name}",
            visit_id_data_key="visit_id",
            visit_label="visit",
            **visit_kwargs,
        )
        sent_count += v_sent
        skipped_count += v_skip
        dedup_skipped += v_dedup

        fv_sent, fv_skip, fv_dedup = _process_upcoming_visit_reminders(
            visits=field_visits,
            assignee_notification_type=NotificationType.FIELD_VISIT_REMINDER,
            reception_notification_type=NotificationType.RECEPTION_FIELD_VISIT_REMINDER,
            reminder_kind_assignee="field_visit",
            reminder_kind_reception="reception_field_visit",
            assignee_email_kind="field_visit",
            reception_email_kind="field_visit_reception",
            assignee_email_title="Field visit follow-up: {lead_name}",
            reception_email_title="Patient field visit reminder: {lead_name}",
            visit_id_data_key="field_visit_id",
            visit_label="field visit",
            **visit_kwargs,
        )
        sent_count += fv_sent
        skipped_count += fv_skip
        dedup_skipped += fv_dedup

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n[DRY RUN] Would send {sent_count} reminder(s), "
                    f"skipped {skipped_count}, dedup_skipped {dedup_skipped}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nSent {sent_count} reminder(s), skipped {skipped_count}, "
                    f"dedup_skipped {dedup_skipped}"
                )
            )
