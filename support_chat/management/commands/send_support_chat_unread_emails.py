from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.event_emails import (
    send_support_chat_unread_to_owner,
    send_support_chat_unread_to_superadmins,
)
from support_chat.models import SupportConversation, SupportMessage


class Command(BaseCommand):
    help = (
        "Email owners and super admins about support chat messages unread past "
        "SUPPORT_CHAT_EMAIL_DELAY_MINUTES. Intended to run every ~4 hours "
        "(see crontab_complete.txt)."
    )

    def handle(self, *args, **options):
        delay_minutes = int(getattr(settings, "SUPPORT_CHAT_EMAIL_DELAY_MINUTES", 15) or 15)
        cutoff = timezone.now() - timedelta(minutes=delay_minutes)
        owner_sent = 0
        admin_sent = 0

        conversations = SupportConversation.objects.select_related("company", "company__owner")
        for conv in conversations:
            tenant_cursor = conv.tenant_last_read_message_id or 0
            support_cursor = conv.support_last_read_message_id or 0

            owner_msg = (
                SupportMessage.objects.filter(
                    conversation=conv,
                    side=SupportMessage.Side.SUPPORT,
                    created_at__lte=cutoff,
                )
                .filter(
                    Q(id__gt=tenant_cursor) & Q(id__gt=conv.tenant_emailed_up_to_id)
                )
                .order_by("id")
                .first()
            )
            if owner_msg:
                with transaction.atomic():
                    locked = SupportConversation.objects.select_for_update().get(pk=conv.pk)
                    if owner_msg.id > locked.tenant_emailed_up_to_id:
                        sent = send_support_chat_unread_to_owner(locked)
                        if sent:
                            locked.tenant_emailed_up_to_id = owner_msg.id
                            locked.save(update_fields=["tenant_emailed_up_to_id", "updated_at"])
                            owner_sent += sent

            tenant_msg = (
                SupportMessage.objects.filter(
                    conversation=conv,
                    side=SupportMessage.Side.TENANT,
                    created_at__lte=cutoff,
                )
                .filter(
                    Q(id__gt=support_cursor) & Q(id__gt=conv.support_emailed_up_to_id)
                )
                .order_by("id")
                .first()
            )
            if tenant_msg:
                with transaction.atomic():
                    locked = SupportConversation.objects.select_for_update().get(pk=conv.pk)
                    if tenant_msg.id > locked.support_emailed_up_to_id:
                        sent = send_support_chat_unread_to_superadmins(locked)
                        if sent:
                            locked.support_emailed_up_to_id = tenant_msg.id
                            locked.save(update_fields=["support_emailed_up_to_id", "updated_at"])
                            admin_sent += sent

        self.stdout.write(
            self.style.SUCCESS(
                f"Support chat unread emails: owners={owner_sent}, admin_notifications={admin_sent}"
            )
        )
