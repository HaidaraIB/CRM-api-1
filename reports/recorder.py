"""
Single write path for report history. Every function swallows and logs its own errors:
a reporting failure must never break saving a lead, a call or a message.
"""
from __future__ import annotations

import logging
from functools import wraps

from django.db import transaction
from django.utils import timezone

from .business_time import business_seconds_between
from .models import ConversationReply, LeadAssignmentPeriod, LeadStatusTransition

logger = logging.getLogger(__name__)


def _safe(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with transaction.atomic():  # savepoint: a DB error here must not poison the caller's transaction
                return fn(*args, **kwargs)
        except Exception:
            logger.exception("reports recorder failed in %s", fn.__name__)
            return None

    return wrapper


@_safe
def record_status_transition(client, from_status, to_status, by=None, source="manual", at=None, reason=""):
    at = at or timezone.now()
    if from_status is not None and to_status is not None and from_status.pk == to_status.pk:
        return None
    previous = (
        LeadStatusTransition.objects.filter(client=client, changed_at__lte=at).order_by("-changed_at", "-id").first()
    )
    since = previous.changed_at if previous else client.created_at
    seconds = max(0, int((at - since).total_seconds())) if since and from_status is not None else None
    row = LeadStatusTransition.objects.create(
        company_id=client.company_id,
        client=client,
        from_status=from_status,
        to_status=to_status,
        from_status_name=from_status.name if from_status else "",
        to_status_name=to_status.name if to_status else "",
        changed_by=by,
        changed_at=at,
        source=source,
        reason=reason or "",
        seconds_in_previous=seconds,
    )
    if by is not None and from_status is not None:
        mark_first_response(client.pk, by.pk, LeadAssignmentPeriod.KIND_STATUS, at)
    return row


@_safe
def close_assignment(client, at=None):
    at = at or timezone.now()
    return LeadAssignmentPeriod.objects.filter(client=client, ended_at__isnull=True).update(ended_at=at)


@_safe
def open_assignment(client, user, by=None, source="manual", at=None):
    """Close the lead's open period and, when ``user`` is set, start a new one. No-op if unchanged."""
    at = at or timezone.now()
    current = LeadAssignmentPeriod.objects.filter(client=client, ended_at__isnull=True).first()
    if current and user is not None and current.user_id == user.pk:
        return current
    if current:
        current.ended_at = max(at, current.assigned_at)
        current.save(update_fields=["ended_at"])
    if user is None:
        return None
    return LeadAssignmentPeriod.objects.create(
        company_id=client.company_id, client=client, user=user, assigned_by=by, source=source, assigned_at=at
    )


@_safe
def mark_first_response(client_id, actor_id, kind, at=None):
    """Credit a first response to the open period, only when ``actor`` is its assignee."""
    if not actor_id:
        return None
    at = at or timezone.now()
    period = (
        LeadAssignmentPeriod.objects.select_related("user", "user__company")
        .filter(client_id=client_id, user_id=actor_id, ended_at__isnull=True, first_response_at__isnull=True)
        .first()
    )
    if period is None or at < period.assigned_at:
        return None
    period.first_response_at = at
    period.first_response_kind = kind
    period.response_seconds = int((at - period.assigned_at).total_seconds())
    period.business_response_seconds = business_seconds_between(
        period.assigned_at, at, period.user, period.user.company
    )
    period.save(
        update_fields=["first_response_at", "first_response_kind", "response_seconds", "business_response_seconds"]
    )
    return period


@_safe
def record_conversation_reply(client, channel, replied_by, replied_at, last_inbound_at):
    """Record that ``replied_by`` answered an unanswered inbound message from ``last_inbound_at``."""
    if last_inbound_at is None or replied_at <= last_inbound_at:
        return None
    company = client.company
    return ConversationReply.objects.create(
        company_id=client.company_id,
        client=client,
        channel=channel,
        inbound_at=last_inbound_at,
        replied_at=replied_at,
        replied_by=replied_by,
        wall_seconds=int((replied_at - last_inbound_at).total_seconds()),
        business_seconds=business_seconds_between(last_inbound_at, replied_at, replied_by, company),
    )


def record_bulk_assignments(clients, by=None, source="bulk", at=None):
    """For ``bulk_update`` assignment paths, which fire no signals. Each client already carries its new assignee."""
    at = at or timezone.now()
    for client in clients:
        open_assignment(client, client.assigned_to, by=by, source=source, at=client.assigned_at or at)
