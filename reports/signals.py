"""
Feeds the report-history tables from model saves. ``.update()`` / ``bulk_create`` paths bypass
signals and call ``reports.recorder`` explicitly instead.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from crm.models import Client, ClientCall, ClientFieldVisit, ClientTask, ClientVisit, Deal
from integrations.models import LeadSMSMessage, LeadWhatsAppMessage

from . import recorder
from .models import ConversationReply, LeadAssignmentPeriod

logger = logging.getLogger(__name__)

K = LeadAssignmentPeriod


def _actor(instance):
    return getattr(instance, "_report_actor", None) or getattr(instance, "_notification_actor", None)


def _source(instance, actor):
    explicit = getattr(instance, "_report_source", None)
    if explicit:
        return explicit
    return "manual" if actor is not None else "system"


@receiver(pre_save, sender=Client, dispatch_uid="reports_client_pre_save")
def stash_old_client_state(sender, instance, **kwargs):
    instance._report_old = None
    if not instance.pk:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not ({"status", "assigned_to"} & set(update_fields)):
        return
    try:
        instance._report_old = (
            Client.objects.filter(pk=instance.pk).values("status_id", "assigned_to_id").first()
        )
    except Exception:
        logger.exception("reports: could not read previous client state")


@receiver(post_save, sender=Client, dispatch_uid="reports_client_post_save")
def record_client_changes(sender, instance, created, **kwargs):
    actor = _actor(instance) or (instance.created_by if created else None)
    source = _source(instance, actor)
    now = timezone.now()
    if created:
        if instance.status_id:
            recorder.record_status_transition(
                instance, None, instance.status, by=actor, source=source, at=instance.created_at or now
            )
        if instance.assigned_to_id:
            recorder.open_assignment(
                instance, instance.assigned_to, by=actor, source=source, at=instance.assigned_at or now
            )
        return

    old = getattr(instance, "_report_old", None)
    if not old:
        return
    if old["status_id"] != instance.status_id:
        from settings.models import LeadStatus

        previous = LeadStatus.objects.filter(pk=old["status_id"]).first() if old["status_id"] else None
        recorder.record_status_transition(
            instance,
            previous,
            instance.status,
            by=actor,
            source=source,
            at=instance.status_entered_at or now,
            reason=getattr(instance, "_status_change_reason", "") or "",
        )
    if old["assigned_to_id"] != instance.assigned_to_id:
        recorder.open_assignment(
            instance, instance.assigned_to, by=actor, source=source, at=instance.assigned_at or now
        )


def _credit(instance, kind, user_field, at):
    uid = getattr(instance, user_field, None)
    if uid and getattr(instance, "client_id", None):
        recorder.mark_first_response(instance.client_id, uid, kind, min(at, timezone.now()))


@receiver(post_save, sender=ClientCall, dispatch_uid="reports_call_first_response")
def call_first_response(sender, instance, created, **kwargs):
    if created:
        _credit(instance, K.KIND_CALL, "created_by_id", instance.call_datetime or instance.created_at)


@receiver(post_save, sender=ClientTask, dispatch_uid="reports_task_first_response")
def task_first_response(sender, instance, created, **kwargs):
    if created:
        _credit(instance, K.KIND_TASK, "created_by_id", instance.created_at)


@receiver(post_save, sender=ClientVisit, dispatch_uid="reports_visit_first_response")
def visit_first_response(sender, instance, created, **kwargs):
    if created:
        _credit(instance, K.KIND_VISIT, "created_by_id", instance.created_at)


@receiver(post_save, sender=ClientFieldVisit, dispatch_uid="reports_field_visit_first_response")
def field_visit_first_response(sender, instance, created, **kwargs):
    if created:
        _credit(instance, K.KIND_VISIT, "created_by_id", instance.created_at)


@receiver(post_save, sender=Deal, dispatch_uid="reports_deal_first_response")
def deal_first_response(sender, instance, created, **kwargs):
    if created:
        _credit(instance, K.KIND_DEAL, "employee_id", instance.created_at)


def _handle_message(instance, model, channel, kind):
    if instance.direction == model.DIRECTION_INBOUND or not instance.created_by_id:
        return
    _credit(instance, kind, "created_by_id", instance.created_at)
    last = (
        model.objects.filter(client_id=instance.client_id, created_at__lt=instance.created_at)
        .exclude(pk=instance.pk)
        .order_by("-created_at", "-pk")
        .first()
    )
    if last is not None and last.direction == model.DIRECTION_INBOUND:
        recorder.record_conversation_reply(
            instance.client, channel, instance.created_by, instance.created_at, last.created_at
        )


@receiver(post_save, sender=LeadWhatsAppMessage, dispatch_uid="reports_whatsapp_message")
def whatsapp_message(sender, instance, created, **kwargs):
    if created:
        _handle_message(instance, LeadWhatsAppMessage, ConversationReply.CHANNEL_WHATSAPP, K.KIND_WHATSAPP)


@receiver(post_save, sender=LeadSMSMessage, dispatch_uid="reports_sms_message")
def sms_message(sender, instance, created, **kwargs):
    if created:
        _handle_message(instance, LeadSMSMessage, ConversationReply.CHANNEL_SMS, K.KIND_SMS)
