"""Integrations app signals."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from integrations.models import LeadWhatsAppMessage
from integrations.whatsapp_conversation_state import apply_inbound_message_rules


@receiver(post_save, sender=LeadWhatsAppMessage)
def lead_whatsapp_message_inbound_state(sender, instance, created, **kwargs):
    if kwargs.get("raw"):
        return
    if not created:
        return
    if instance.direction != LeadWhatsAppMessage.DIRECTION_INBOUND:
        return
    apply_inbound_message_rules(instance)
