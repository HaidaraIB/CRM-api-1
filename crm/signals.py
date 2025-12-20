from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Client, ClientTask
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

