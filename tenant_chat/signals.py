from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import ChatMessage


@receiver(post_save, sender=ChatMessage)
def bump_conversation_timestamp(sender, instance, **kwargs):
    """Keep conversation ordering aligned with latest activity."""
    from .models import ChatConversation

    ChatConversation.objects.filter(pk=instance.conversation_id).update(
        updated_at=timezone.now()
    )
