"""
Convert an inbox conversation into a CRM lead.

This is the moment a conversation stops being a DM and becomes a lead. Everything
before it is deliberately lead-less, so this is also the only place the plan's
max_clients quota is charged for a social contact.

Instagram and Messenger DMs carry no phone number, so the created lead is usually
phone-less. That is safe against the company-wide unique phone key (its constraint
is conditional on a non-empty normalized value), but it does mean the lead will
not be matched by find_client_by_phone later. Never fabricate a placeholder phone
to work around that — it would consume the unique key for the whole company.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from crm.lead_defaults import get_default_lead_status_id
from subscriptions.entitlements import require_quota

from ..models import IntegrationLog, SocialChannel

logger = logging.getLogger(__name__)

CHANNEL_LABELS = {
    SocialChannel.INSTAGRAM: 'Instagram DM',
    SocialChannel.MESSENGER: 'Facebook Messenger',
}


class ConvertError(Exception):
    """Client-safe failure with an error_key and HTTP status."""

    def __init__(self, error_key: str, message: str, status_code: int = 400, details=None):
        super().__init__(message)
        self.error_key = error_key
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _resolve_assignee(company, actor, assigned_to_id, auto_assign):
    from accounts.models import User
    from crm.assignment import get_auto_assign_employee, has_assignable_employee

    if assigned_to_id:
        user = User.objects.filter(
            id=assigned_to_id, company=company, is_active=True
        ).first()
        if not user:
            raise ConvertError(
                'social_assignee_not_found',
                'The selected assignee is not an active user in this company.',
            )
        return user

    if auto_assign and has_assignable_employee(company):
        # None is an acceptable outcome (everyone off-shift) — an unassigned lead
        # beats refusing the conversion and losing the agent's work.
        return get_auto_assign_employee(company)

    return None


@transaction.atomic
def convert_conversation_to_lead(conversation, actor, payload: dict):
    """
    Create (or link) a crm.Client for this conversation.

    Returns {'client': Client, 'duplicate': bool, 'assignee': User|None}.
    Raises ConvertError for anything the agent must act on.
    """
    from crm.models import Client, ClientEvent, ClientPhoneNumber

    company = conversation.company

    if conversation.client_id:
        raise ConvertError(
            'social_already_converted',
            'This conversation is already linked to a lead.',
            status_code=409,
            details={'client_id': conversation.client_id},
        )

    phone = (payload.get('phone') or '').strip()

    # Charge the quota before anything is created.
    try:
        require_quota(
            company,
            'max_clients',
            current_count=Client.objects.filter(company=company).count(),
            requested_delta=1,
            message='Lead limit reached for this company plan.',
            error_key='plan_quota_max_clients_exceeded',
        )
    except ValidationError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        raise ConvertError(
            detail.get('error_key') or 'plan_quota_max_clients_exceeded',
            detail.get('error') or 'Lead limit reached for this company plan.',
            status_code=403,
        )

    # An agent who types a known phone must link, not fork a second lead.
    if phone:
        from .phone_match import find_client_by_phone

        existing = find_client_by_phone(company, phone)
        if existing:
            conversation.client = existing
            conversation.converted_at = timezone.now()
            conversation.converted_by = actor
            conversation.assigned_to = existing.assigned_to
            conversation.save(
                update_fields=[
                    'client', 'converted_at', 'converted_by', 'assigned_to', 'updated_at'
                ]
            )
            return {'client': existing, 'duplicate': True, 'assignee': existing.assigned_to}

    assignee = _resolve_assignee(
        company,
        actor,
        payload.get('assigned_to'),
        bool(payload.get('auto_assign', True)),
    )

    contact = conversation.contact
    channel_label = CHANNEL_LABELS.get(conversation.channel, conversation.channel)
    name = (
        (payload.get('name') or '').strip()
        or contact.name
        or contact.username
        or f"{channel_label} {contact.external_id[-6:]}"
    )

    external_lead_id = f"{conversation.channel}:{contact.external_id}"

    try:
        client = Client.objects.create(
            company=company,
            name=name,
            source=conversation.channel,
            integration_account=conversation.connection.integration_account,
            external_lead_id=external_lead_id,
            phone_number=phone or None,
            notes=(payload.get('notes') or '').strip(),
            priority=(payload.get('priority') or 'medium'),
            type=(payload.get('type') or 'fresh'),
            status_id=payload.get('status_id') or get_default_lead_status_id(company),
            communication_way_id=payload.get('communication_way_id') or None,
            campaign_id=payload.get('campaign_id') or None,
            assigned_to=assignee,
            assigned_at=timezone.now() if assignee else None,
            created_by=actor,
        )
    except IntegrityError:
        # Double-click, or a racing second agent. external_lead_id is unique per
        # company, so resolve to the row that won rather than erroring.
        client = Client.objects.filter(
            company=company, external_lead_id=external_lead_id
        ).first()
        if client is None:
            raise ConvertError(
                'social_convert_failed',
                'Could not create the lead. Please try again.',
                status_code=500,
            )
        conversation.client = client
        conversation.converted_at = timezone.now()
        conversation.converted_by = actor
        conversation.save(
            update_fields=['client', 'converted_at', 'converted_by', 'updated_at']
        )
        return {'client': client, 'duplicate': True, 'assignee': client.assigned_to}
    except Exception as exc:
        # Client.save() raises when the chosen assignee is off-shift / on leave.
        raise ConvertError(
            'social_assignee_unavailable',
            str(getattr(exc, 'message', None) or exc) or 'The selected assignee is unavailable.',
        )

    # Only create a phone row when there actually is a phone. A placeholder would
    # consume the company-wide unique key.
    if phone:
        ClientPhoneNumber.objects.create(
            client=client, phone_number=phone, phone_type='mobile', is_primary=True
        )

    ClientEvent.objects.create(
        client=client,
        event_type='created',
        new_value=channel_label,
        notes=(
            f"Converted from {channel_label} conversation with "
            f"{contact.username or contact.external_id}"
        ),
        created_by=actor,
    )

    conversation.client = client
    conversation.assigned_to = assignee
    conversation.converted_at = timezone.now()
    conversation.converted_by = actor
    conversation.save(
        update_fields=[
            'client', 'assigned_to', 'converted_at', 'converted_by', 'updated_at'
        ]
    )

    _notify_after_commit(conversation, client, assignee, actor)

    if conversation.connection.integration_account_id:
        IntegrationLog.objects.create(
            account=conversation.connection.integration_account,
            action='meta_inbox_lead_converted',
            status='success',
            message=f"Conversation {conversation.id} converted to lead {client.id}",
            response_data={
                'conversation_id': conversation.id,
                'client_id': client.id,
                'channel': conversation.channel,
                'assigned_to_id': assignee.id if assignee else None,
            },
        )

    return {'client': client, 'duplicate': False, 'assignee': assignee}


def _notify_after_commit(conversation, client, assignee, actor):
    """Reuse the same notification paths every other inbound lead source uses."""

    def _fire():
        try:
            from crm.signals import notify_lead_assignment_change

            if assignee:
                notify_lead_assignment_change(
                    client=client,
                    old_assignee=None,
                    new_assignee=assignee,
                    actor=actor,
                )
        except Exception:
            logger.exception("Social convert: assignment notification failed")

        try:
            from .inbound_lead import notify_owner_new_lead

            notify_owner_new_lead(client.company, client)
        except Exception:
            logger.exception("Social convert: owner notification failed")

    transaction.on_commit(_fire)
