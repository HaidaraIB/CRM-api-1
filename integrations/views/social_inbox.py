"""
Omni-Channel Inbox HTTP endpoints (Instagram Direct + Facebook Messenger).

These are function views with their own permission classes on purpose. The
call-center deny rules in accounts.permissions (DenyCallCenterNonLeadAPI,
DenyCallCenterWriteExceptCreate) are mounted only on the CRM viewsets in
crm/views.py, so granting CALL_CENTER access here cannot widen its access to
deals, tasks, or lead editing.
"""

from __future__ import annotations

import logging

from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import (
    CanConvertSocialConversation,
    CanUseSocialInbox,
    HasActiveSubscription,
)
from crm_saas_api.responses import error_response, success_response
from settings.models import SystemSettings
from sync.cache import invalidate_badges
from sync.conditional import conditional_token, not_modified, tag
from sync.version import bump_company_slice

from ..models import (
    IntegrationAccount,
    IntegrationPlatform,
    MetaInboxConnection,
    SocialConversation,
    SocialMessage,
    WhatsAppConversationStatus,
)
from ..policy import get_effective_integration_policy, get_plan_integration_access
from ..services.meta_inbox_connections import (
    PageConnectError,
    check_connection_health,
    connect_page,
    disconnect_page,
    list_grantable_pages,
)
from ..services.meta_inbox_media import social_kind_for_upload, validate_social_upload
from ..services.meta_inbox_send import SendWindowClosed, describe_window, send_message
from ..services.social_lead import ConvertError, convert_conversation_to_lead
from ..social_conversation_state import sweep_expired_snoozes
from ..social_inbox_access import (
    filter_social_conversations_queryset,
    require_social_conversation_access,
    user_sees_all_social_conversations,
)

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 200
ORDERING_WHITELIST = frozenset(
    {'last_message_at', '-last_message_at', 'created_at', '-created_at'}
)


def _inbox_gate(company):
    """Plan + admin policy gate. Returns an error Response, or None when allowed."""
    plan = get_plan_integration_access(company, 'meta_inbox')
    if not plan['enabled']:
        return error_response(
            plan['message'],
            code='plan_integration_disabled',
            status_code=status.HTTP_403_FORBIDDEN,
        )
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform='meta_inbox',
    )
    if not effective['enabled']:
        return error_response(
            effective['message'],
            code='integration_disabled',
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return None


def _serialize_connection(connection: MetaInboxConnection) -> dict:
    """Never includes page_access_token — that credential stays server-side."""
    return {
        'id': connection.id,
        'page_id': connection.page_id,
        'page_name': connection.page_name,
        'ig_user_id': connection.ig_user_id,
        'ig_username': connection.ig_username,
        'status': connection.status,
        'error_message': connection.error_message,
        'instagram_subscribed': connection.instagram_subscribed,
        'messenger_subscribed': connection.messenger_subscribed,
        'subscribed_fields': connection.subscribed_fields or [],
        'last_webhook_at': connection.last_webhook_at,
        'created_at': connection.created_at,
        'updated_at': connection.updated_at,
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def social_inbox_connections(request):
    """
    GET  — connected Pages plus the Pages the tenant could still connect.
    POST — connect one Page: resolve its token, link Instagram, subscribe webhooks.

    Owner/admin only: this is account configuration, not inbox usage.
    """
    user = request.user
    company = getattr(user, 'company', None)
    if not company:
        return error_response(
            'No company associated with this user.',
            code='company_required',
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not user.is_admin():
        return error_response(
            'Only the account owner can manage inbox connections.',
            code='meta_inbox_admin_only',
            status_code=status.HTTP_403_FORBIDDEN,
        )

    gate = _inbox_gate(company)
    if gate is not None:
        return gate

    account = IntegrationAccount.objects.filter(
        company=company,
        platform=IntegrationPlatform.META_INBOX,
    ).first()

    if request.method == 'GET':
        # Disconnected Pages keep their conversation history, but they are not
        # a live inbox source — omit them so "Remove Page" actually clears the row.
        connections = (
            MetaInboxConnection.objects.filter(company=company)
            .exclude(status='disconnected')
            .order_by('-created_at')
        )
        listed_ids = {c.page_id for c in connections}
        available = []
        if account and account.status == 'connected':
            available = [
                page for page in list_grantable_pages(account)
                if page['id'] not in listed_ids
            ]
        return success_response(
            data={
                'account': (
                    {
                        'id': account.id,
                        # The tenant-set label, same field the Lead Ads account row
                        # shows and its Edit button renames.
                        'name': account.name,
                        'status': account.status,
                        'external_account_name': account.external_account_name,
                        'error_message': account.error_message,
                    }
                    if account
                    else None
                ),
                'connections': [_serialize_connection(c) for c in connections],
                'available_pages': available,
            }
        )

    if not account or account.status != 'connected':
        return error_response(
            'Connect the Meta Inbox integration first.',
            code='meta_inbox_not_connected',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    page_id = str((request.data or {}).get('page_id') or '').strip()
    if not page_id:
        return error_response(
            'page_id is required.',
            code='meta_inbox_page_required',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        connection = connect_page(account, page_id)
    except PageConnectError as exc:
        return error_response(
            exc.message,
            code=exc.error_key,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        logger.exception("Meta Inbox: unexpected failure connecting page %s", page_id)
        return error_response(
            'Could not connect this Page. Please try again.',
            code='meta_inbox_connect_failed',
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    return success_response(
        data={'connection': _serialize_connection(connection)},
        message='Page connected.',
        status_code=status.HTTP_201_CREATED,
    )


@api_view(['DELETE', 'POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def social_inbox_connection_detail(request, pk: int):
    """
    DELETE — disconnect a Page (keeps its conversation history).
    POST    — re-run the subscription health check for this Page.
    """
    user = request.user
    company = getattr(user, 'company', None)
    if not company or not user.is_admin():
        return error_response(
            'Only the account owner can manage inbox connections.',
            code='meta_inbox_admin_only',
            status_code=status.HTTP_403_FORBIDDEN,
        )

    connection = MetaInboxConnection.objects.filter(pk=pk, company=company).first()
    if not connection:
        return error_response(
            'Connection not found.',
            code='meta_inbox_connection_not_found',
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if request.method == 'DELETE':
        disconnect_page(connection)
        return success_response(
            data={'connection': _serialize_connection(connection)},
            message='Page disconnected.',
        )

    result = check_connection_health(connection)
    connection.refresh_from_db()
    return success_response(
        data={'health': result, 'connection': _serialize_connection(connection)}
    )


# --- Inbox reading -------------------------------------------------------------


def _serialize_contact(contact) -> dict:
    return {
        'id': contact.id,
        'external_id': contact.external_id,
        'name': contact.name,
        'username': contact.username,
        'display_name': contact.display_name,
        'profile_pic_url': contact.profile_pic_url,
    }


def _serialize_lead_message(message) -> dict:
    """
    One social message as the lead Timeline needs it.

    Narrower than `_serialize_message` on purpose. The timeline renders a text
    line per message, so it needs the body, who said it and when, plus enough to
    group rows into the right thread. It does not need delivery state, reactions,
    echo flags or attachment geometry, and shipping those into a lead view would
    put message metadata in front of roles that only ever asked for a lead.

    `attachment_kind` survives because a media-only message has an empty body —
    the clients turn it into a "[photo]"-style placeholder, exactly as the
    WhatsApp thread already does.
    """
    author = message.created_by
    conversation = message.conversation
    return {
        'id': message.id,
        'conversation': conversation.id,
        'channel': conversation.channel,
        'contact_name': conversation.contact.display_name,
        'direction': message.direction,
        'body': message.body,
        'attachment_kind': message.attachment_kind,
        'is_voice_note': message.is_voice_note,
        'created_by': author.id if author else None,
        'created_by_username': author.username if author else None,
        'sent_at': message.sent_at,
        'created_at': message.created_at,
    }


def _serialize_conversation(conversation) -> dict:
    client = conversation.client
    assigned = conversation.assigned_to
    return {
        'id': conversation.id,
        'channel': conversation.channel,
        'status': conversation.status,
        'is_starred': conversation.is_starred,
        'is_unsubscribed': conversation.is_unsubscribed,
        'snoozed_until': conversation.snoozed_until,
        'unread_count': conversation.unread_count,
        'last_message_at': conversation.last_message_at,
        'last_message_direction': conversation.last_message_direction,
        'last_message_preview': conversation.last_message_preview,
        'last_inbound_at': conversation.last_inbound_at,
        'contact': _serialize_contact(conversation.contact),
        'connection': {
            'id': conversation.connection_id,
            'page_name': conversation.connection.page_name,
            'ig_username': conversation.connection.ig_username,
        },
        'assigned_to': (
            {
                'id': assigned.id,
                'username': assigned.username,
                'full_name': assigned.get_full_name() or assigned.username,
            }
            if assigned
            else None
        ),
        # A non-null client is what the UI shows as the "Lead #N" chip and what
        # makes the convert action a no-op.
        'client': (
            {'id': client.id, 'name': client.name} if client else None
        ),
        'converted_at': conversation.converted_at,
        'created_at': conversation.created_at,
    }


def _serialize_message(message) -> dict:
    author = message.created_by
    return {
        'id': message.id,
        'direction': message.direction,
        'body': message.body,
        'external_message_id': message.external_message_id,
        'is_echo': message.is_echo,
        'is_read': message.is_read,
        'reaction': message.reaction,
        'delivery_status': message.delivery_status,
        'delivery_error': message.delivery_error,
        'error_key': message.error_key,
        'attachment_kind': message.attachment_kind,
        'attachment_mime': message.attachment_mime,
        'attachment_size': message.attachment_size,
        'attachment_width': message.attachment_width,
        'attachment_height': message.attachment_height,
        'original_filename': message.original_filename,
        'has_attachment': bool(message.attachment),
        'is_voice_note': message.is_voice_note,
        'location_latitude': message.location_latitude,
        'location_longitude': message.location_longitude,
        'location_name': message.location_name,
        'location_address': message.location_address,
        'created_by_username': author.username if author else None,
        'sent_at': message.sent_at,
        'created_at': message.created_at,
    }


def _conversation_base_queryset(user):
    return filter_social_conversations_queryset(
        user,
        SocialConversation.objects.select_related(
            'contact', 'connection', 'assigned_to', 'client'
        ),
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_conversations_list(request):
    """
    GET /integrations/inbox/conversations/

    Filters: channel, status, assignment (all|mine|unassigned), agent,
    converted (yes|no), starred, unreplied, search, ordering, limit, offset.
    """
    company = getattr(request.user, 'company', None)
    if not company:
        return error_response(
            'No company associated with this user.',
            code='company_required',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    gate = _inbox_gate(company)
    if gate is not None:
        return gate

    # A snooze expiring changes what the list shows but writes no row, so sweep
    # BEFORE minting the token or the list would 304 with the thread still hidden.
    if sweep_expired_snoozes(company):
        bump_company_slice('inbox', company.id)

    # The whole query string is the variant so switching filters cannot 304 with
    # the previous filter's rows.
    token = conditional_token(
        request.user,
        slices=('inbox',),
        include_user_seq=True,
        variant='&'.join(f"{k}={v}" for k, v in sorted(request.query_params.items())),
    )
    cached = not_modified(request, token)
    if cached is not None:
        return cached

    qs = _conversation_base_queryset(request.user)

    channel = (request.query_params.get('channel') or 'all').strip().lower()
    assignment = (request.query_params.get('assignment') or 'all').strip().lower()
    agent_id = (request.query_params.get('agent') or '').strip()
    converted = (request.query_params.get('converted') or '').strip().lower()
    starred = (request.query_params.get('starred') or '').lower() in ('1', 'true', 'yes')
    unreplied = (request.query_params.get('unreplied') or '').lower() in ('1', 'true', 'yes')
    search = (request.query_params.get('search') or '').strip()
    status_filter = (request.query_params.get('status') or 'all').strip().lower()
    ordering = (request.query_params.get('ordering') or '-last_message_at').strip()

    if channel in ('instagram', 'messenger'):
        qs = qs.filter(channel=channel)

    if assignment == 'mine':
        qs = qs.filter(assigned_to_id=request.user.id)
    elif assignment == 'unassigned':
        qs = qs.filter(assigned_to__isnull=True)

    if agent_id:
        # A scoped employee filtering by another agent would be probing for
        # conversations they cannot otherwise see.
        if not user_sees_all_social_conversations(request.user):
            return error_response(
                'Not allowed to filter by agent',
                code='social_agent_filter_forbidden',
                status_code=status.HTTP_403_FORBIDDEN,
            )
        try:
            qs = qs.filter(assigned_to_id=int(agent_id))
        except (TypeError, ValueError):
            return error_response(
                'agent must be a user id',
                code='bad_request',
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    if converted == 'yes':
        qs = qs.filter(client__isnull=False)
    elif converted == 'no':
        qs = qs.filter(client__isnull=True)

    if starred:
        qs = qs.filter(is_starred=True)
    if unreplied:
        qs = qs.filter(last_message_direction=SocialMessage.DIRECTION_INBOUND)
    if search:
        qs = qs.filter(
            Q(contact__name__icontains=search)
            | Q(contact__username__icontains=search)
            | Q(last_message_preview__icontains=search)
        )

    # Counts are computed BEFORE the status filter so the filter rail can show
    # every bucket's size while one bucket is selected.
    status_counts = {
        row['status']: row['n']
        for row in qs.values('status').annotate(n=Count('id'))
    }
    for value in WhatsAppConversationStatus.values:
        status_counts.setdefault(value, 0)

    assignment_counts = {
        'all': qs.count(),
        'mine': qs.filter(assigned_to_id=request.user.id).count(),
        'unassigned': qs.filter(assigned_to__isnull=True).count(),
        'unconverted': qs.filter(client__isnull=True).count(),
    }

    if status_filter in WhatsAppConversationStatus.values:
        qs = qs.filter(status=status_filter)

    if ordering not in ORDERING_WHITELIST:
        ordering = '-last_message_at'
    qs = qs.order_by(ordering, '-id')

    try:
        limit = min(int(request.query_params.get('limit') or 50), MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(int(request.query_params.get('offset') or 0), 0)
    except (TypeError, ValueError):
        offset = 0

    total = qs.count()
    rows = list(qs[offset:offset + limit])

    return tag(
        success_response(
            data={
                'results': [_serialize_conversation(c) for c in rows],
                'count': total,
                'limit': limit,
                'offset': offset,
                'status_counts': status_counts,
                'assignment_counts': assignment_counts,
            }
        ),
        token,
    )


def _get_conversation_or_error(request, pk):
    """Returns (conversation, error_response). Denied reads answer 404, never 403."""
    conversation = (
        SocialConversation.objects.select_related(
            'contact', 'connection', 'assigned_to', 'client'
        )
        .filter(pk=pk)
        .first()
    )
    if conversation is None:
        return None, error_response(
            'Conversation not found.',
            code='social_conversation_not_found',
            status_code=status.HTTP_404_NOT_FOUND,
        )
    err = require_social_conversation_access(request.user, conversation)
    if err:
        return None, error_response(
            'Conversation not found.',
            code=err,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return conversation, None


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_conversation_messages(request, pk: int):
    """GET /integrations/inbox/conversations/<pk>/messages/ — oldest-first thread."""
    company = getattr(request.user, 'company', None)
    gate = _inbox_gate(company) if company else None
    if gate is not None:
        return gate

    conversation, err = _get_conversation_or_error(request, pk)
    if err is not None:
        return err

    token = conditional_token(
        request.user,
        slices=('inbox',),
        include_user_seq=True,
        variant=f"conv={pk}&"
        + '&'.join(f"{k}={v}" for k, v in sorted(request.query_params.items())),
    )
    cached = not_modified(request, token)
    if cached is not None:
        return cached

    try:
        limit = min(int(request.query_params.get('limit') or 200), MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        limit = 200

    # Newest `limit` rows, then flipped so the thread renders oldest-first.
    rows = list(
        SocialMessage.objects.filter(conversation=conversation)
        .select_related('created_by')
        .order_by('-created_at', '-id')[:limit]
    )
    rows.reverse()

    return tag(
        success_response(
            data={
                'results': [_serialize_message(m) for m in rows],
                'conversation': _serialize_conversation(conversation),
            }
        ),
        token,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_lead_messages(request):
    """
    GET /integrations/inbox/lead-messages/?client=<id>

    Every social message on every conversation converted to this lead, for the
    lead Timeline. Mirrors `GET /integrations/whatsapp/messages/?client=` — the
    timeline is assembled client-side from one endpoint per source, and this is
    the source that was missing.

    A lead can hold more than one conversation (an Instagram DM and a Messenger
    thread both converted onto it), so each row carries its `conversation` id and
    `channel`. The clients group on those; without them two different people's
    threads would render as one conversation.

    Scoped by the same ACL as the rest of the inbox, deliberately: reception and
    data entry can open a lead but get nothing here, which is the existing
    product rule, and reusing `filter_social_conversations_queryset` means there
    is one place where "who may read a DM" is decided rather than two.
    """
    company = getattr(request.user, 'company', None)
    if not company:
        return error_response(
            'No company associated with this user.',
            code='company_required',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    gate = _inbox_gate(company)
    if gate is not None:
        return gate

    client_id = request.query_params.get('client')
    if not client_id:
        return error_response(
            'client is required.',
            code='bad_request',
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return error_response(
            'client must be an integer.',
            code='bad_request',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = conditional_token(
        request.user,
        slices=('inbox',),
        include_user_seq=True,
        variant=f"lead={client_id}",
    )
    cached = not_modified(request, token)
    if cached is not None:
        return cached

    # An unconverted conversation has client=None, so this also can't leak a
    # thread that was never attached to this lead.
    conversations = _conversation_base_queryset(request.user).filter(
        company=company, client_id=client_id
    )

    try:
        limit = min(int(request.query_params.get('limit') or 200), MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        limit = 200

    rows = (
        SocialMessage.objects.filter(conversation__in=conversations)
        .select_related('created_by', 'conversation', 'conversation__contact')
        .order_by('-created_at', '-id')[:limit]
    )

    return tag(
        success_response(
            data={'results': [_serialize_lead_message(m) for m in rows]}
        ),
        token,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_mark_conversation_read(request):
    """POST /integrations/inbox/conversations/mark-read/ — body: {conversation: id}"""
    conversation_id = (request.data or {}).get('conversation')
    if not conversation_id:
        return error_response(
            'conversation is required.',
            code='bad_request',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    conversation, err = _get_conversation_or_error(request, conversation_id)
    if err is not None:
        return err

    updated = SocialMessage.objects.filter(
        conversation=conversation,
        direction=SocialMessage.DIRECTION_INBOUND,
        is_read=False,
    ).update(is_read=True)

    if updated or conversation.unread_count:
        SocialConversation.objects.filter(pk=conversation.pk).update(unread_count=0)
        invalidate_badges(request.user.id)
        # Both writes above are bulk update()s, which fire no post_save, so the
        # signals in sync/signals.py cannot see them. Without this explicit bump
        # the conversation list would be answered 304 with the old unread counts.
        bump_company_slice('inbox', conversation.company_id)

    return success_response(data={'marked': updated, 'conversation_id': conversation.id})


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_update_conversation_state(request):
    """
    POST /integrations/inbox/conversations/state/
    Body: {conversation, status?, is_starred?, is_unsubscribed?, snoozed_until?}
    """
    data = request.data or {}
    conversation, err = _get_conversation_or_error(request, data.get('conversation'))
    if err is not None:
        return err

    update_fields = []

    if 'status' in data:
        new_status = str(data.get('status') or '').strip().lower()
        if new_status not in WhatsAppConversationStatus.values:
            return error_response(
                'Unknown conversation status.',
                code='social_invalid_status',
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        conversation.status = new_status
        conversation.status_changed_at = timezone.now()
        conversation.status_changed_by = request.user
        update_fields += ['status', 'status_changed_at', 'status_changed_by']
        if new_status != WhatsAppConversationStatus.SNOOZED:
            conversation.snoozed_until = None
            update_fields.append('snoozed_until')

    if 'snoozed_until' in data:
        conversation.snoozed_until = data.get('snoozed_until') or None
        if conversation.snoozed_until:
            conversation.status = WhatsAppConversationStatus.SNOOZED
            update_fields.append('status')
        update_fields.append('snoozed_until')

    if 'is_starred' in data:
        conversation.is_starred = bool(data.get('is_starred'))
        update_fields.append('is_starred')

    if 'is_unsubscribed' in data:
        conversation.is_unsubscribed = bool(data.get('is_unsubscribed'))
        update_fields.append('is_unsubscribed')

    if not update_fields:
        return error_response(
            'Nothing to update.',
            code='bad_request',
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # save() (not update()) so the SocialConversation post_save bumps the slice.
    conversation.save(update_fields=list(set(update_fields)) + ['updated_at'])
    return success_response(data={'conversation': _serialize_conversation(conversation)})


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_unread_count(request):
    """GET /integrations/inbox/unread-count/ — badge source for the sidebar."""
    company = getattr(request.user, 'company', None)
    if not company:
        return success_response(data={'unread': 0})
    gate = _inbox_gate(company)
    if gate is not None:
        return gate

    conversations = filter_social_conversations_queryset(
        request.user, SocialConversation.objects.all()
    )
    total = (
        SocialMessage.objects.filter(
            conversation__in=conversations,
            direction=SocialMessage.DIRECTION_INBOUND,
            is_read=False,
        ).count()
    )
    return success_response(data={'unread': total})


# --- Sending -------------------------------------------------------------------


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_send_window(request):
    """
    GET /integrations/inbox/window/?conversation=<id>

    Lets the composer disable itself with the right hint instead of letting an
    agent type a message Meta will reject.
    """
    conversation, err = _get_conversation_or_error(
        request, request.query_params.get('conversation')
    )
    if err is not None:
        return err
    return success_response(data=describe_window(conversation))


def _persist_and_send(request, conversation, *, text=None, attachment_type=None, upload=None):
    """
    Persist the outbound row first, then call Graph.

    Persisting first means a failed send still leaves a bubble the agent can see
    and retry, rather than silently vanishing.
    """
    message = SocialMessage.objects.create(
        conversation=conversation,
        direction=SocialMessage.DIRECTION_OUTBOUND,
        body=text or '',
        created_by=request.user,
        delivery_status='pending',
        is_read=True,
        sent_at=timezone.now(),
        attachment_kind=attachment_type or None,
    )

    if upload is not None:
        try:
            upload.seek(0)
            message.attachment.save(upload.name, upload, save=False)
            message.attachment_mime = getattr(upload, 'content_type', '') or ''
            message.attachment_size = upload.size
            message.original_filename = upload.name[:200]
            message.save(
                update_fields=[
                    'attachment', 'attachment_mime', 'attachment_size', 'original_filename'
                ]
            )
            upload.seek(0)
        except Exception:
            logger.exception("Meta Inbox: failed storing outbound attachment")

    try:
        result = send_message(
            conversation,
            text=text,
            attachment_type=attachment_type,
            attachment_file=upload,
        )
    except SendWindowClosed:
        # Refused locally — no Graph call was made. Drop the row so a blocked
        # attempt does not litter the thread.
        message.delete()
        return error_response(
            'The reply window for this conversation has closed.',
            code=SendWindowClosed.error_key,
            status_code=status.HTTP_403_FORBIDDEN,
            details={'window': describe_window(conversation)},
        )

    if result['ok']:
        message.external_message_id = result['mid'] or ''
        message.delivery_status = 'sent'
        message.save(update_fields=['external_message_id', 'delivery_status'])

        conversation.last_message_at = message.sent_at
        conversation.last_message_direction = SocialMessage.DIRECTION_OUTBOUND
        conversation.last_message_preview = (text or attachment_type or '')[:280]
        conversation.save(
            update_fields=[
                'last_message_at', 'last_message_direction', 'last_message_preview', 'updated_at'
            ]
        )
        payload = {'message': _serialize_message(message)}
        temp_id = (request.data or {}).get('client_temp_id')
        if temp_id:
            payload['client_temp_id'] = temp_id
        return success_response(data=payload, status_code=status.HTTP_201_CREATED)

    message.delivery_status = 'failed'
    message.delivery_error = (result['error_message'] or '')[:512]
    message.error_key = result['error_key'] or ''
    message.save(update_fields=['delivery_status', 'delivery_error', 'error_key'])

    return error_response(
        result['error_message'] or 'Could not send the message.',
        code=result['error_key'] or 'social_send_failed',
        # The serialized row lets the client render a failed bubble with Retry.
        details={'message': _serialize_message(message)},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_send_message(request):
    """POST /integrations/inbox/send/ — body: {conversation, text, client_temp_id?}"""
    data = request.data or {}
    conversation, err = _get_conversation_or_error(request, data.get('conversation'))
    if err is not None:
        return err

    gate = _inbox_gate(conversation.company)
    if gate is not None:
        return gate

    text = (data.get('text') or '').strip()
    if not text:
        return error_response(
            'text is required.',
            code='bad_request',
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if conversation.is_unsubscribed:
        return error_response(
            'This contact has opted out of messages.',
            code='social_contact_unsubscribed',
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return _persist_and_send(request, conversation, text=text)


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_send_media(request):
    """POST /integrations/inbox/send-media/ — multipart: conversation, file, kind?"""
    data = request.data or {}
    conversation, err = _get_conversation_or_error(request, data.get('conversation'))
    if err is not None:
        return err

    gate = _inbox_gate(conversation.company)
    if gate is not None:
        return gate

    upload = request.FILES.get('file')
    if upload is None:
        return error_response(
            'file is required.',
            code='bad_request',
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if conversation.is_unsubscribed:
        return error_response(
            'This contact has opted out of messages.',
            code='social_contact_unsubscribed',
            status_code=status.HTTP_403_FORBIDDEN,
        )

    error_key = validate_social_upload(upload, kind=data.get('kind'))
    if error_key:
        return error_response(
            'This file cannot be sent on this channel.',
            code=error_key,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    kind = social_kind_for_upload(upload, requested=data.get('kind'))
    return _persist_and_send(
        request,
        conversation,
        text=(data.get('text') or '').strip() or None,
        attachment_type=kind,
        upload=upload,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasActiveSubscription, CanUseSocialInbox])
def social_message_attachment(request, pk: int):
    """
    GET /integrations/inbox/messages/<pk>/attachment/

    Served through the API rather than MEDIA_URL so the same ACL that guards the
    thread guards its media.
    """
    message = (
        SocialMessage.objects.select_related(
            'conversation', 'conversation__client', 'conversation__connection'
        )
        .filter(pk=pk)
        .first()
    )
    if message is None or not message.attachment:
        return error_response(
            'Attachment not found.',
            code='social_attachment_not_found',
            status_code=status.HTTP_404_NOT_FOUND,
        )

    err = require_social_conversation_access(request.user, message.conversation)
    if err:
        return error_response(
            'Attachment not found.',
            code='social_attachment_not_found',
            status_code=status.HTTP_404_NOT_FOUND,
        )

    response = FileResponse(
        message.attachment.open('rb'),
        content_type=message.attachment_mime or 'application/octet-stream',
    )
    if message.original_filename:
        response['Content-Disposition'] = f'inline; filename="{message.original_filename}"'
    # private: this is an ACL-filtered payload, no shared cache may hold it.
    response['Cache-Control'] = 'private, max-age=3600'
    return response


# --- Convert to lead -----------------------------------------------------------


@api_view(['POST'])
@permission_classes([
    IsAuthenticated,
    HasActiveSubscription,
    CanUseSocialInbox,
    CanConvertSocialConversation,
])
def social_convert_conversation(request, pk: int):
    """
    POST /integrations/inbox/conversations/<pk>/convert/

    Turns a triaged conversation into a CRM lead and assigns an employee. This is
    the whole point of the inbox for a call-center agent.
    """
    conversation, err = _get_conversation_or_error(request, pk)
    if err is not None:
        return err

    gate = _inbox_gate(conversation.company)
    if gate is not None:
        return gate

    try:
        result = convert_conversation_to_lead(conversation, request.user, request.data or {})
    except ConvertError as exc:
        return error_response(
            exc.message,
            code=exc.error_key,
            details=exc.details,
            status_code=exc.status_code,
        )

    conversation.refresh_from_db()
    client = result['client']
    return success_response(
        data={
            'client_id': client.id,
            'client_name': client.name,
            'patient_file_number': getattr(client, 'patient_file_number', None),
            'assigned_to_id': result['assignee'].id if result['assignee'] else None,
            'duplicate': result['duplicate'],
            'conversation': _serialize_conversation(conversation),
        },
        message='Conversation converted to a lead.',
        status_code=status.HTTP_201_CREATED,
    )
