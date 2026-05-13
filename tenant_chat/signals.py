from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from companies.models import Company

from .models import ChatConversation, ChatMessage, ensure_company_group_conversation


@receiver(post_save, sender=ChatMessage)
def bump_conversation_timestamp(sender, instance, **kwargs):
    """Keep conversation ordering aligned with latest activity."""
    ChatConversation.objects.filter(pk=instance.conversation_id).update(
        updated_at=timezone.now()
    )


@receiver(post_save, sender=Company)
def ensure_company_group_chat_thread(sender, instance, **kwargs):
    ensure_company_group_conversation(instance)
