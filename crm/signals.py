from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Client, ClientTask, Deal
from accounts.models import User
from notifications.services import NotificationService
from notifications.models import NotificationType
import logging

logger = logging.getLogger(__name__)


def get_least_busy_employee(company):
    """
    Get the employee with the least number of assigned clients (Round Robin)
    """
    employees = User.objects.filter(
        company=company,
        role='employee',
        is_active=True
    )
    
    if not employees.exists():
        return None
    
    # Get employee with minimum assigned clients
    employees_with_counts = []
    for employee in employees:
        count = Client.objects.filter(
            company=company,
            assigned_to=employee
        ).count()
        employees_with_counts.append((employee, count))
    
    # Sort by count and return the one with least clients
    employees_with_counts.sort(key=lambda x: x[1])
    return employees_with_counts[0][0] if employees_with_counts else None


@receiver(post_save, sender=Client)
def auto_assign_client(sender, instance, created, **kwargs):
    """
    Auto assign client to employee when created if auto_assign_enabled is True
    """
    if not created:
        return  # Only for new clients
    
    if not instance.company:
        return
    
    # Check if auto assign is enabled for this company
    if not instance.company.auto_assign_enabled:
        return
    
    # If already assigned, don't override
    if instance.assigned_to:
        return
    
    # Get the least busy employee
    employee = get_least_busy_employee(instance.company)
    
    if employee:
        instance.assigned_to = employee
        instance.assigned_at = timezone.now()
        instance.save(update_fields=['assigned_to', 'assigned_at'])


@receiver(post_save, sender=ClientTask)
def update_last_contacted(sender, instance, created, **kwargs):
    """
    Update client's last_contacted_at when a new ClientTask is created
    """
    if created and instance.client:
        instance.client.last_contacted_at = timezone.now()
        instance.client.save(update_fields=['last_contacted_at'])


# ==================== Notification Signals ====================

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
        
        # If lead came from a campaign, notify admin
        if instance.campaign and instance.company and instance.company.owner:
            campaign_name = instance.campaign.name
            NotificationService.send_notification(
                user=instance.company.owner,
                notification_type=NotificationType.NEW_LEAD,
                data={
                    'lead_id': instance.id,
                    'lead_name': instance.name,
                    'campaign_name': campaign_name,
                }
            )
    except Exception as e:
        logger.error(f"Error sending notification for new lead: {e}")


@receiver(pre_save, sender=Client)
def notify_lead_status_changed(sender, instance, **kwargs):
    """Send notification when lead status changes"""
    if instance.pk:  # Only for existing instances
        try:
            old_instance = Client.objects.get(pk=instance.pk)
            old_status_id = old_instance.status.id if old_instance.status else None
            new_status_id = instance.status.id if instance.status else None
            
            if old_status_id != new_status_id and instance.status:
                # Status changed
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
        except Client.DoesNotExist:
            pass
        except Exception as e:
            logger.error(f"Error in notify_lead_status_changed: {e}")


@receiver(pre_save, sender=Client)
def notify_lead_assigned(sender, instance, **kwargs):
    """Send notification when lead is assigned to a different employee"""
    if instance.pk:  # Only for existing instances
        try:
            old_instance = Client.objects.get(pk=instance.pk)
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
        except Client.DoesNotExist:
            pass
        except Exception as e:
            logger.error(f"Error in notify_lead_assigned: {e}")


@receiver(post_save, sender=Client)
def notify_lead_updated(sender, instance, created, **kwargs):
    """Send notification when lead is updated"""
    if created:
        return  # Already handled by notify_new_lead
    
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
    pass


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
        except Deal.DoesNotExist:
            pass
        except Exception as e:
            logger.error(f"Error in notify_deal_closed: {e}")

