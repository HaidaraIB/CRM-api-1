"""
Freshness contract for GET /sync/digest/.

The digest answers most polls with a 304 based on a version token, so a count
that changes without bumping its counter would go unnoticed until the token's
time bucket rolls over. Keeping every bump here, on model signals rather than
spread across the views that happen to write these rows, means a new write path
(a management command, an admin action, a webhook, a data migration) is covered
by default instead of by remembering.

Each receiver bumps the narrowest scope that can see the change — see the scope
notes in sync/version.py. The writes these hook are all rare next to the polls
they save, which is what makes the trade worth it.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from crm.models import LeadArrival
from integrations.models import LeadWhatsAppMessage, WhatsAppCall
from notifications.models import Notification
from platform_content.models import NewsPost, UserNewsReadState
from tenant_chat.models import ChatConversationReadState, ChatMessage

from .version import bump_company, bump_global, bump_user


# --- user scope: notifications_unread, pbx_screen_pop, news_unread -------------


@receiver(post_save, sender=Notification)
@receiver(post_delete, sender=Notification)
def notification_changed(sender, instance, **kwargs):
    """Covers create, read/unread, and the deleted_at soft delete alike."""
    if kwargs.get("raw"):
        return
    bump_user(instance.user_id)


@receiver(post_save, sender=UserNewsReadState)
def news_read_state_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_user(instance.user_id)


@receiver(post_save, sender=ChatConversationReadState)
def chat_read_state_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_user(instance.user_id)


# --- company scope: chats, calls, arrivals -------------------------------------


@receiver(post_save, sender=WhatsAppCall)
def whatsapp_call_changed(sender, instance, **kwargs):
    """Status transitions in and out of RINGING drive the incoming-call toast."""
    if kwargs.get("raw"):
        return
    bump_company(instance.company_id)


@receiver(post_save, sender=LeadArrival)
def lead_arrival_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_company(instance.company_id)


@receiver(m2m_changed, sender=LeadArrival.notified_users.through)
def lead_arrival_recipients_changed(sender, instance, action, **kwargs):
    """
    Recipients are attached after the arrival row exists, so the post_save bump
    above fires while the arrival is still addressed to nobody. Without this the
    people actually being alerted would wait for the next time bucket.
    """
    if action in ("post_add", "post_remove", "post_clear"):
        bump_company(getattr(instance, "company_id", None))


@receiver(post_save, sender=ChatMessage)
@receiver(post_delete, sender=ChatMessage)
def chat_message_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    # Cached on the instance wherever the message was built with a conversation
    # object, which is every send path today.
    conversation = instance.conversation
    bump_company(getattr(conversation, "company_id", None))


@receiver(post_save, sender=LeadWhatsAppMessage)
@receiver(post_delete, sender=LeadWhatsAppMessage)
def lead_whatsapp_message_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    client = instance.client
    bump_company(getattr(client, "company_id", None))


# --- global scope: platform-wide news ------------------------------------------


@receiver(post_save, sender=NewsPost)
def news_post_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_global()
