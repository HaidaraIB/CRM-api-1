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

Company-visible changes go through ``bump_company_slice``, which moves both the
narrow slice a client watches and the coarse counter the ETag is built from. Never
call ``bump_company`` directly from here: moving the ETag without the slice makes
the digest rebuild while telling clients nothing changed, so they would keep
showing stale lists.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from accounts.models import SupervisorPermission
from companies.models import Company
from crm.models import LeadArrival
from subscriptions.models import Subscription
from integrations.models import (
    LeadWhatsAppMessage,
    SocialConversation,
    SocialMessage,
    WhatsAppCall,
)
from notifications.models import Notification
from platform_content.models import NewsPost, UserNewsReadState
from support_chat.models import SupportConversation, SupportMessage
from tenant_chat.models import ChatConversationReadState, ChatMessage

from .version import (
    bump_company_slice,
    bump_conversation,
    bump_global,
    bump_support_conversation,
    bump_support_inbox,
    bump_user,
    bump_user_access,
)

User = get_user_model()

_USER_ACCESS_FIELDS = (
    "role",
    "is_active",
    "company_id",
    "whatsapp_chat_enabled",
    "whatsapp_call_enabled",
) + tuple(f.name for f in User._meta.fields if f.name.startswith("can_"))

_USER_ACCESS_SKIP_UPDATE_FIELDS = frozenset(
    {
        "last_login",
        "last_seen_at",
        "last_seen_source",
        "work_last_ping_at",
        "failed_login_attempts",
        "lockout_until",
        "password",
    }
)


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
    # Also the thread: the message list renders read receipts from the *other*
    # participant's cursor, so without this a "seen" tick would not appear until
    # the next safety bucket.
    bump_conversation(instance.conversation_id)


# --- company scope: chats, calls, arrivals -------------------------------------


@receiver(post_save, sender=WhatsAppCall)
def whatsapp_call_changed(sender, instance, **kwargs):
    """Status transitions in and out of RINGING drive the incoming-call toast."""
    if kwargs.get("raw"):
        return
    bump_company_slice("calls", instance.company_id)


@receiver(post_save, sender=LeadArrival)
def lead_arrival_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_company_slice("arrivals", instance.company_id)


@receiver(m2m_changed, sender=LeadArrival.notified_users.through)
def lead_arrival_recipients_changed(sender, instance, action, **kwargs):
    """
    Recipients are attached after the arrival row exists, so the post_save bump
    above fires while the arrival is still addressed to nobody. Without this the
    people actually being alerted would wait for the next time bucket.
    """
    if action in ("post_add", "post_remove", "post_clear"):
        bump_company_slice("arrivals", getattr(instance, "company_id", None))


@receiver(post_save, sender=SupportMessage)
@receiver(post_delete, sender=SupportMessage)
def support_chat_message_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    conversation = instance.conversation
    bump_support_conversation(conversation.id)
    bump_support_inbox()
    bump_company_slice("support_chat", getattr(conversation, "company_id", None))


@receiver(post_save, sender=SupportConversation)
def support_conversation_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_support_conversation(instance.id)
    bump_support_inbox()
    bump_company_slice("support_chat", instance.company_id)


@receiver(post_save, sender=ChatMessage)
@receiver(post_delete, sender=ChatMessage)
def chat_message_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_conversation(instance.conversation_id)
    # Cached on the instance wherever the message was built with a conversation
    # object, which is every send path today.
    conversation = instance.conversation
    bump_company_slice("tenant_chat", getattr(conversation, "company_id", None))


@receiver(post_save, sender=LeadWhatsAppMessage)
@receiver(post_delete, sender=LeadWhatsAppMessage)
def lead_whatsapp_message_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    client = instance.client
    bump_company_slice("chat", getattr(client, "company_id", None))


@receiver(post_save, sender=SocialMessage)
@receiver(post_delete, sender=SocialMessage)
def social_message_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    conversation = instance.conversation
    bump_company_slice("inbox", getattr(conversation, "company_id", None))


@receiver(post_save, sender=SocialConversation)
def social_conversation_changed(sender, instance, **kwargs):
    """
    Needed on top of the message receiver: triage, assignment and convert-to-lead
    all change what the conversation list renders without writing any message.
    """
    if kwargs.get("raw"):
        return
    bump_company_slice("inbox", getattr(instance, "company_id", None))


# --- global scope: platform-wide news ------------------------------------------


@receiver(post_save, sender=NewsPost)
def news_post_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_global()


# --- access / account: permissions, subscription, company status ---------------


@receiver(pre_save, sender=User)
def user_access_pre_save(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    if not instance.pk:
        instance._access_fields_changed = True
        return
    try:
        previous = User.objects.get(pk=instance.pk)
    except User.DoesNotExist:
        instance._access_fields_changed = True
        return
    instance._access_fields_changed = any(
        getattr(previous, name) != getattr(instance, name) for name in _USER_ACCESS_FIELDS
    )


@receiver(post_save, sender=User)
def user_access_post_save(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None:
        if set(update_fields).issubset(_USER_ACCESS_SKIP_UPDATE_FIELDS):
            return
    if getattr(instance, "_access_fields_changed", True):
        bump_user_access(instance.id)


@receiver(post_save, sender=SupervisorPermission)
def supervisor_permission_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_user_access(instance.user_id)


@receiver(post_save, sender=Subscription)
def subscription_account_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_company_slice("account", instance.company_id)


@receiver(post_save, sender=Company)
def company_account_changed(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    bump_company_slice("account", instance.id)
