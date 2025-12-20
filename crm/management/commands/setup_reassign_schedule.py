"""
Management command to setup the re-assign scheduled task
Run this once after migrations to schedule the re-assign task
"""
from django.core.management.base import BaseCommand
from django_q.tasks import schedule
from django_q.models import Schedule
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Setup the re-assign scheduled task to run every hour'

    def handle(self, *args, **options):
        # Delete any existing schedules with similar names or incorrect function names
        from django_q.models import Failure
        
        # Delete schedules
        deleted_schedules = Schedule.objects.filter(
            name__in=['re_assign_inactive_clients', 'check_reassignments']
        ).delete()
        
        # Delete any failed tasks with incorrect function names
        deleted_failures = Failure.objects.filter(
            func__icontains='check_reassignments'
        ).delete()
        
        if deleted_schedules[0] > 0 or deleted_failures[0] > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'Deleted {deleted_schedules[0]} schedule(s) and {deleted_failures[0]} failure(s)...'
                )
            )
        
        # Schedule the task to run every hour
        schedule(
            'crm.tasks.re_assign_inactive_clients',
            name='re_assign_inactive_clients',
            schedule_type=Schedule.HOURLY,
            next_run=timezone.now() + timedelta(minutes=5),  # Start in 5 minutes
            repeats=-1,  # Repeat indefinitely
        )
        
        self.stdout.write(
            self.style.SUCCESS('Successfully scheduled re-assign task to run every hour')
        )

