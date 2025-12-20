from django.utils import timezone
from datetime import timedelta
from django.db import models
from .models import Client, ClientEvent
from accounts.models import User


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
        
        # Find clients that:
        # 1. Are assigned to an employee
        # 2. Haven't been contacted since threshold_time
        # 3. Either never had last_contacted_at or it's older than threshold
        # 4. assigned_at is older than threshold (meaning they've been assigned for at least the threshold time)
        clients_to_reassign = Client.objects.filter(
            company=company,
            assigned_to__isnull=False,
            assigned_at__isnull=False,
            assigned_at__lt=threshold_time
        ).filter(
            # Either last_contacted_at is None (never contacted) or older than threshold
            models.Q(last_contacted_at__isnull=True) | 
            models.Q(last_contacted_at__lt=threshold_time)
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

