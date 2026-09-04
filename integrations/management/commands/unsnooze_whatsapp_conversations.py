"""
Reopen WhatsApp conversations whose snooze has expired.

Usage:
    python manage.py unsnooze_whatsapp_conversations
"""

from django.core.management.base import BaseCommand

from companies.models import Company
from integrations.models import WhatsAppConversationState, WhatsAppConversationStatus
from integrations.whatsapp_conversation_state import sweep_expired_snoozes
from sync.version import bump_company_slice


class Command(BaseCommand):
    help = "Reopen snoozed WhatsApp conversations past snoozed_until"

    def handle(self, *args, **options):
        from django.utils import timezone

        now = timezone.now()
        company_ids = (
            WhatsAppConversationState.objects.filter(
                status=WhatsAppConversationStatus.SNOOZED,
                snoozed_until__lte=now,
            )
            .values_list("company_id", flat=True)
            .distinct()
        )
        total = 0
        for company_id in company_ids:
            try:
                company = Company.objects.get(pk=company_id)
            except Company.DoesNotExist:
                continue
            n = sweep_expired_snoozes(company)
            if n:
                bump_company_slice("chat", company_id)
                total += n
        self.stdout.write(self.style.SUCCESS(f"Unsnoozed {total} conversation(s)"))
