from django.db.models.signals import post_save, pre_save
from django.db.models import Count
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone
from crm.availability import user_accepts_new_assignments
from .models import Client, ClientTask, ClientCall, ClientEvent, ClientVisit, Deal
from accounts.models import User, Role
from notifications.services import NotificationService
from notifications.models import NotificationType
from notifications.team_activity import notify_owner_team_activity
import logging

logger = logging.getLogger(__name__)

_INTEGRATION_AUTO_ASSIGN_NOTES = {
    "tiktok": "Auto-assigned from TikTok Lead Gen",
    "meta_lead_form": "Auto-assigned from Meta Lead Form",
    "whatsapp": "Auto-assigned from WhatsApp",
}


def _integration_auto_assign_event_notes(client):
    return _INTEGRATION_AUTO_ASSIGN_NOTES.get(
        (client.source or "").strip(), "Auto-assigned from integration"
    )


def get_least_busy_employee(company):
    """
    Get the employee with the least number of assigned clients (Round Robin),
    skipping anyone on their weekly day off (company-local calendar).
    """
    queryset = (
        User.objects.filter(
            company=company,
            role=Role.EMPLOYEE.value,
            is_active=True,
        )
        .annotate(client_count=Count("assigned_clients"))
        .order_by("client_count", "id")
        .select_related("company")
    )
    for employee in queryset:
        if user_accepts_new_assignments(employee):
            return employee
    return None


def get_next_data_entry_round_robin_employee(company):
    """
    Return the next active employee for data-entry lead assignment using
    a persisted circular pointer on Company.
    """
    from companies.models import Company

    with transaction.atomic():
        locked_company = Company.objects.select_for_update().get(pk=company.pk)
        employees = list(
            User.objects.filter(
                company=locked_company,
                role=Role.EMPLOYEE.value,
                is_active=True,
            )
            .select_related("company")
            .order_by("id")
        )
        employees = [e for e in employees if user_accepts_new_assignments(e)]

        if not employees:
            if locked_company.last_data_entry_assigned_employee_id is not None:
                locked_company.last_data_entry_assigned_employee = None
                locked_company.save(update_fields=["last_data_entry_assigned_employee"])
            return None

        employee_ids = [employee.id for employee in employees]
        last_id = locked_company.last_data_entry_assigned_employee_id

        if last_id in employee_ids:
            current_index = employee_ids.index(last_id)
            next_index = (current_index + 1) % len(employee_ids)
        else:
            next_index = 0

        selected_employee = employees[next_index]
        locked_company.last_data_entry_assigned_employee = selected_employee
        locked_company.save(update_fields=["last_data_entry_assigned_employee"])
        return selected_employee


@receiver(post_save, sender=Client)
def auto_assign_client(sender, instance, created, **kwargs):
    """
    Auto-assign new clients to the least busy employee when company.auto_assign_enabled
    is True. Applies to all new leads (manual, API, and integration sources such as
    Meta, TikTok, WhatsApp) so the Lead Assignment toggle is the single switch.
    """
    if not created:
        return  # Only for new clients

    if not instance.company_id:
        return

    company = instance.company
    from_integration = bool(instance.integration_account_id)
    if not company.auto_assign_enabled:
        return

    # If already assigned, don't override
    if instance.assigned_to_id:
        return

    employee = get_least_busy_employee(company)
    if not employee:
        return

    instance.assigned_to = employee
    instance.assigned_at = timezone.now()
    instance.save(update_fields=["assigned_to", "assigned_at"])

    if from_integration:
        ClientEvent.objects.create(
            client=instance,
            event_type="assignment",
            old_value="Unassigned",
            new_value=employee.get_full_name() or employee.username,
            notes=_integration_auto_assign_event_notes(instance),
        )


@receiver(post_save, sender=ClientTask)
def update_last_contacted_from_task(sender, instance, created, **kwargs):
    """
    Update client's last_contacted_at when a new ClientTask is created
    """
    if created and instance.client:
        # Use update to avoid triggering signals again
        Client.objects.filter(pk=instance.client.pk).update(last_contacted_at=timezone.now())
        if instance.created_by_id:
            notify_owner_team_activity(
                instance.created_by,
                instance.client.company,
                action="task_created",
                lead_id=instance.client_id,
                lead_name=instance.client.name,
            )


@receiver(post_save, sender=ClientCall)
def update_last_contacted_from_call(sender, instance, created, **kwargs):
    """
    Update client's last_contacted_at when a new ClientCall is created
    """
    if created and instance.client:
        # Use update to avoid triggering signals again
        Client.objects.filter(pk=instance.client.pk).update(last_contacted_at=timezone.now())
        if instance.created_by_id:
            notify_owner_team_activity(
                instance.created_by,
                instance.client.company,
                action="call_logged",
                lead_id=instance.client_id,
                lead_name=instance.client.name,
            )


@receiver(post_save, sender=ClientVisit)
def on_client_visit_post_save(sender, instance, created, **kwargs):
    """
    After a visit is logged: bump last_contacted_at and set lead status to Visited
    for real_estate / services companies.
    """
    if not created or not instance.client_id:
        return
    from crm.models import Client
    from settings.lead_status_automation import (
        ensure_visited_lead_status,
        get_visited_lead_status,
    )

    Client.objects.filter(pk=instance.client.pk).update(last_contacted_at=timezone.now())
    if instance.created_by_id:
        notify_owner_team_activity(
            instance.created_by,
            instance.client.company,
            action="visit_logged",
            lead_id=instance.client_id,
            lead_name=instance.client.name,
        )

    client = instance.client
    company = getattr(client, "company", None)
    spec = getattr(company, "specialization", None) if company else None
    if spec not in ("real_estate", "services"):
        return

    visited = get_visited_lead_status(company) or ensure_visited_lead_status(company)
    if visited:
        now = timezone.now()
        Client.objects.filter(pk=client.pk).update(
            status_id=visited.pk,
            status_entered_at=now,
        )


# ==================== Notification Signals ====================

@receiver(post_save, sender=Client)
def align_status_entered_at_on_client_create(sender, instance, created, **kwargs):
    """Match status_entered_at to created_at for new leads (consistent with migration backfill)."""
    if not created:
        return
    Client.objects.filter(pk=instance.pk).update(status_entered_at=instance.created_at)


@receiver(post_save, sender=Client)
def notify_new_lead(sender, instance, created, **kwargs):
    """Send notification when a new lead is created"""
    if not created:
        return
    
    try:
        # Send notification to assigned employee if exists
        if instance.assigned_to:
            NotificationService.send_notification(
                user=instance.assigned_to,
                notification_type=NotificationType.LEAD_ASSIGNED,
                data={
                    'lead_id': instance.id,
                    'lead_name': instance.name,
                }
            )
        
    except Exception as e:
        logger.error(f"Error sending notification for new lead: {e}")


@receiver(pre_save, sender=Client)
def handle_client_pre_save(sender, instance, **kwargs):
    """Combined handler for pre-save changes to avoid multiple DB hits."""
    if not instance.pk:  # Only for existing instances
        return
        
    try:
        old_instance = Client.objects.get(pk=instance.pk)
    except Client.DoesNotExist:
        return
    except Exception as e:
        logger.error(f"Error in handle_client_pre_save: {e}")
        return

    # --- Check for status changes ---
    old_status_id = old_instance.status.id if old_instance.status else None
    new_status_id = instance.status.id if instance.status else None

    if old_status_id != new_status_id:
        instance.status_entered_at = timezone.now()

    if old_status_id != new_status_id and instance.status:
        # Status changed - update last_contacted_at
        instance.last_contacted_at = timezone.now()

        # Send notification
        if instance.assigned_to:
            try:
                NotificationService.send_notification(
                    user=instance.assigned_to,
                    notification_type=NotificationType.LEAD_STATUS_CHANGED,
                    data={
                        'lead_id': instance.id,
                        'lead_name': instance.name,
                        'new_status': instance.status.name,
                    }
                )
            except Exception as e:
                logger.error(f"Error sending status change notification: {e}")

    # --- Check for assignment changes ---
    old_assigned_id = old_instance.assigned_to.id if old_instance.assigned_to else None
    new_assigned_id = instance.assigned_to.id if instance.assigned_to else None
    
    if old_assigned_id != new_assigned_id:
        # Assigned to different employee
        if instance.assigned_to:
            try:
                NotificationService.send_notification(
                    user=instance.assigned_to,
                    notification_type=NotificationType.LEAD_ASSIGNED,
                    data={
                        'lead_id': instance.id,
                        'lead_name': instance.name,
                    }
                )
            except Exception as e:
                logger.error(f"Error sending assignment notification: {e}")
        
        # Notify old employee if exists
        if old_instance.assigned_to and old_instance.assigned_to != instance.assigned_to:
            try:
                NotificationService.send_notification(
                    user=old_instance.assigned_to,
                    notification_type=NotificationType.LEAD_TRANSFERRED,
                    data={
                        'lead_id': instance.id,
                        'lead_name': instance.name,
                        'from_employee': old_instance.assigned_to.username,
                        'to_employee': instance.assigned_to.username if instance.assigned_to else '',
                    }
                )
            except Exception as e:
                logger.error(f"Error sending transfer notification: {e}")


@receiver(post_save, sender=Client)
def notify_lead_updated(sender, instance, created, **kwargs):
    """Update last_contacted_at when lead is updated (any field change)"""
    if created:
        return  # Already handled by notify_new_lead
    
    # Update last_contacted_at for any update to the client
    # This ensures that any action taken on the lead will prevent leadNoFollowUp notifications
    if not instance.last_contacted_at or (timezone.now() - instance.last_contacted_at).total_seconds() > 60:
        # Only update if it's been more than 1 minute since last update to avoid excessive updates
        instance.last_contacted_at = timezone.now()
        # Use update_fields to avoid triggering signals again
        Client.objects.filter(pk=instance.pk).update(last_contacted_at=timezone.now())
    
    # Notify assigned employee (only if updated, not on status/assignment changes)
    # We skip this to avoid duplicate notifications
    # Uncomment if you want to notify on every update
    # if instance.assigned_to:
    #     try:
    #         NotificationService.send_notification(
    #             user=instance.assigned_to,
    #             notification_type=NotificationType.LEAD_UPDATED,
    #             title='تم تحديث العميل',
    #             body=f'تم تحديث معلومات العميل {instance.name}',
    #             data={
    #                 'lead_id': instance.id,
    #                 'lead_name': instance.name,
    #             }
    #         )
    #     except Exception as e:
    #         logger.error(f"Error sending update notification: {e}")


@receiver(post_save, sender=Deal)
def notify_deal_created(sender, instance, created, **kwargs):
    """Send notification when a deal is created"""
    if not created:
        return
    
    try:
        # Notify the employee who created the deal
        if instance.employee:
            NotificationService.send_notification(
                user=instance.employee,
                notification_type=NotificationType.DEAL_CREATED,
                data={
                    'deal_id': instance.id,
                    'deal_title': f'{instance.client.name} - {instance.value or 0}',
                }
            )
        
        # Notify company owner (only if different from employee)
        if instance.company and instance.company.owner and instance.company.owner != instance.employee:
            NotificationService.send_notification(
                user=instance.company.owner,
                notification_type=NotificationType.DEAL_CREATED,
                data={
                    'deal_id': instance.id,
                    'deal_title': f'{instance.client.name} - {instance.value or 0}',
                }
            )
    except Exception as e:
        logger.error(f"Error sending deal created notification: {e}")


@receiver(pre_save, sender=Deal)
def notify_deal_closed(sender, instance, **kwargs):
    """Send notification when a deal is closed"""
    if instance.pk:  # Only for existing instances
        try:
            old_instance = Deal.objects.get(pk=instance.pk)
            if old_instance.stage != instance.stage and instance.stage == 'won':
                # Deal closed/won
                if instance.employee:
                    try:
                        NotificationService.send_notification(
                            user=instance.employee,
                            notification_type=NotificationType.DEAL_CLOSED,
                            data={
                                'deal_id': instance.id,
                                'deal_title': f'{instance.client.name} - {instance.value or 0}',
                                'value': str(instance.value or 0),
                            }
                        )
                    except Exception as e:
                        logger.error(f"Error sending deal closed notification: {e}")
                notify_owner_team_activity(
                    instance.employee,
                    instance.company,
                    action="deal_won",
                    deal_id=instance.id,
                    deal_title=f"{instance.client.name} - {instance.value or 0}",
                    lead_id=instance.client_id,
                    lead_name=instance.client.name,
                    value=str(instance.value or 0),
                )
        except Deal.DoesNotExist:
            pass
        except Exception as e:
            logger.error(f"Error in notify_deal_closed: {e}")


@receiver(post_save, sender=Client)
def schedule_welcome_sms_on_new_client(sender, instance, created, **kwargs):
    """Queue automated welcome SMS after DB commit (phones may be added in the same request)."""
    if not created:
        return
    if not instance.company_id:
        return
    from integrations.services.lead_created_sms import schedule_lead_created_welcome_sms

    schedule_lead_created_welcome_sms(instance.pk)


@receiver(post_save, sender=ClientEvent)
def notify_owner_on_client_event(sender, instance, created, **kwargs):
    """Notify owner on high-signal client events generated by user actions."""
    if not created or not instance.created_by_id:
        return

    if instance.event_type not in {"status_change", "assignment", "edit"}:
        return

    lead_name = instance.client.name
    lead_id = instance.client_id

    if instance.event_type == "status_change":
        notify_owner_team_activity(
            instance.created_by,
            instance.client.company,
            action="status_change",
            lead_id=lead_id,
            lead_name=lead_name,
            old_status=instance.old_value or "",
            new_status=instance.new_value or "",
        )
    elif instance.event_type == "assignment":
        notify_owner_team_activity(
            instance.created_by,
            instance.client.company,
            action="assignment",
            lead_id=lead_id,
            lead_name=lead_name,
            old_assignee=instance.old_value or "",
            new_assignee=instance.new_value or "",
        )
    else:
        detail = (instance.notes or "").strip()
        if not detail and (instance.old_value or instance.new_value):
            detail = " → ".join(
                [p for p in (instance.old_value or "", instance.new_value or "") if p]
            ).strip()
        notify_owner_team_activity(
            instance.created_by,
            instance.client.company,
            action="edit",
            lead_id=lead_id,
            lead_name=lead_name,
            detail=detail or "",
        )

