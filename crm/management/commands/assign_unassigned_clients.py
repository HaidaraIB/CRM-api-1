"""
Management command to assign unassigned clients to employees
Use this to assign existing unassigned clients when auto_assign is enabled
"""
from django.core.management.base import BaseCommand
from crm.models import Client
from crm.signals import get_least_busy_employee
from django.utils import timezone


class Command(BaseCommand):
    help = 'Assign unassigned clients to employees based on auto_assign settings'

    def add_arguments(self, parser):
        parser.add_argument(
            '--company-id',
            type=int,
            help='Only process clients for a specific company ID',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be assigned without actually assigning',
        )

    def handle(self, *args, **options):
        company_id = options.get('company_id')
        dry_run = options.get('dry_run', False)

        # Get unassigned clients
        queryset = Client.objects.filter(assigned_to__isnull=True)
        
        if company_id:
            queryset = queryset.filter(company_id=company_id)
        
        unassigned_clients = queryset.select_related('company')

        if not unassigned_clients.exists():
            self.stdout.write(
                self.style.SUCCESS('No unassigned clients found.')
            )
            return

        assigned_count = 0
        skipped_count = 0

        for client in unassigned_clients:
            if not client.company:
                self.stdout.write(
                    self.style.WARNING(f'Client {client.id} ({client.name}) has no company. Skipping.')
                )
                skipped_count += 1
                continue

            # Only assign if auto_assign is enabled for this company
            if not client.company.auto_assign_enabled:
                if not company_id:  # Only show this if processing all companies
                    self.stdout.write(
                        self.style.WARNING(
                            f'Client {client.id} ({client.name}) - Company "{client.company.name}" '
                            f'has auto_assign disabled. Skipping.'
                        )
                    )
                skipped_count += 1
                continue

            # Get the least busy employee
            employee = get_least_busy_employee(client.company)

            if not employee:
                self.stdout.write(
                    self.style.WARNING(
                        f'Client {client.id} ({client.name}) - No employees found in company "{client.company.name}". Skipping.'
                    )
                )
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would assign Client {client.id} ({client.name}) to {employee.get_full_name() or employee.username}'
                    )
                )
            else:
                client.assigned_to = employee
                client.assigned_at = timezone.now()
                client.save(update_fields=['assigned_to', 'assigned_at'])
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Assigned Client {client.id} ({client.name}) to {employee.get_full_name() or employee.username}'
                    )
                )
            
            assigned_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n[DRY RUN] Would assign {assigned_count} client(s), skipped {skipped_count}'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nAssigned {assigned_count} client(s), skipped {skipped_count}'
                )
            )

