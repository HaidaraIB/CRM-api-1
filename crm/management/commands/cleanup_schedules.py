"""
Management command to cleanup old or incorrect schedules
"""
from django.core.management.base import BaseCommand
from django_q.models import Schedule


class Command(BaseCommand):
    help = 'Cleanup old or incorrect scheduled tasks'

    def handle(self, *args, **options):
        # Delete schedules with incorrect function names
        incorrect_schedules = Schedule.objects.filter(
            func__icontains='check_reassignments'
        )
        
        count = incorrect_schedules.count()
        incorrect_schedules.delete()
        
        if count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'Deleted {count} incorrect schedule(s)')
            )
        else:
            self.stdout.write(
                self.style.WARNING('No incorrect schedules found')
            )

