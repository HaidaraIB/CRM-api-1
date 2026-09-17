"""
Re-enqueue campaign batches stuck in SENDING with no active worker lock.

Usage:
    python manage.py resume_stale_campaign_batches
"""

from django.core.management.base import BaseCommand

from integrations.tasks import resume_stale_campaign_batches


class Command(BaseCommand):
    help = "Resume campaign batches stuck in SENDING after a worker timeout or crash"

    def handle(self, *args, **options):
        count = resume_stale_campaign_batches()
        self.stdout.write(self.style.SUCCESS(f"Re-enqueued {count} stale campaign batch(es)"))
