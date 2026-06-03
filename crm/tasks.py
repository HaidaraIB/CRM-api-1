from django.utils import timezone
from datetime import timedelta
from django.db.models import Exists, OuterRef, Q
from .models import (
    Client,
    ClientCall,
    ClientEvent,
    ClientFieldVisit,
    ClientTask,
    ClientVisit,
    Deal,
)
from crm.assignment import get_least_busy_employee


def _assignee_acted_since_assignment():
    """
    True when the currently assigned employee logged contact/activity on this
    lead at or after assigned_at (current assignment period only).
    """
    client_id = OuterRef("pk")
    assignee_id = OuterRef("assigned_to")
    since = OuterRef("assigned_at")

    from integrations.models import LeadSMSMessage, LeadWhatsAppMessage

    return (
        Exists(
            ClientEvent.objects.filter(
                client=client_id,
                created_by=assignee_id,
                created_at__gte=since,
            ).exclude(event_type="re_assignment")
        )
        | Exists(
            ClientTask.objects.filter(
                client=client_id,
                created_by=assignee_id,
                created_at__gte=since,
            )
        )
        | Exists(
            ClientCall.objects.filter(
                client=client_id,
                created_by=assignee_id,
            ).filter(
                Q(call_datetime__isnull=False, call_datetime__gte=since)
                | Q(call_datetime__isnull=True, created_at__gte=since)
            )
        )
        | Exists(
            ClientVisit.objects.filter(
                client=client_id,
                created_by=assignee_id,
            ).filter(
                Q(visit_datetime__isnull=False, visit_datetime__gte=since)
                | Q(visit_datetime__isnull=True, created_at__gte=since)
            )
        )
        | Exists(
            ClientFieldVisit.objects.filter(
                client=client_id,
                created_by=assignee_id,
            ).filter(
                Q(visit_datetime__isnull=False, visit_datetime__gte=since)
                | Q(visit_datetime__isnull=True, created_at__gte=since)
            )
        )
        | Exists(
            Deal.objects.filter(
                client=client_id,
                employee=assignee_id,
                created_at__gte=since,
            )
        )
        | Exists(
            LeadWhatsAppMessage.objects.filter(
                client=client_id,
                created_by=assignee_id,
                direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
                created_at__gte=since,
            )
        )
        | Exists(
            LeadSMSMessage.objects.filter(
                client=client_id,
                created_by=assignee_id,
                created_at__gte=since,
            )
        )
    )


def re_assign_inactive_clients():
    """
    Re-assign clients that haven't been contacted within the specified hours
    This function will be called by django-q2 scheduler
    """
    from companies.models import Company
    
    # Get all companies with re_assign_enabled
    companies = Company.objects.filter(re_assign_enabled=True)
    
    total_reassigned = 0
    
    for company in companies:
        hours_threshold = company.re_assign_hours or 24
        threshold_time = timezone.now() - timedelta(hours=hours_threshold)
        
        # Reassign when the current assignee has had the lead for at least the
        # threshold period and logged no contact/activity since assigned_at.
        clients_to_reassign = (
            Client.objects.filter(
                company=company,
                assigned_to__isnull=False,
                assigned_at__isnull=False,
                assigned_at__lt=threshold_time,
            )
            .annotate(_assignee_acted_since_assignment=_assignee_acted_since_assignment())
            .filter(_assignee_acted_since_assignment=False)
        )

        for client in clients_to_reassign:
            # Get new employee (different from current one)
            current_employee = client.assigned_to
            new_employee = get_least_busy_employee(company)
            
            # Only reassign if we have a different employee available
            if new_employee and new_employee != current_employee:
                old_employee_name = current_employee.get_full_name() or current_employee.username if current_employee else "Unassigned"
                new_employee_name = new_employee.get_full_name() or new_employee.username
                
                client.assigned_to = new_employee
                client.assigned_at = timezone.now()
                # Reset last_contacted_at since it's a new assignment
                client.last_contacted_at = None
                client.save(update_fields=['assigned_to', 'assigned_at', 'last_contacted_at'])
                
                # Create event log
                ClientEvent.objects.create(
                    client=client,
                    event_type='re_assignment',
                    old_value=old_employee_name,
                    new_value=new_employee_name,
                    notes=f'تم إعادة التعيين تلقائياً بعد {hours_threshold} ساعة من عدم التواصل'
                )
                
                total_reassigned += 1
    
    return f"تم إعادة تعيين {total_reassigned} عميل"


# Alias for backward compatibility (in case old tasks are still in queue)
def check_reassignments():
    """
    Alias for re_assign_inactive_clients
    This is kept for backward compatibility with old scheduled tasks
    """
    return re_assign_inactive_clients()

