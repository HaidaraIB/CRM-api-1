"""
Connect / disconnect WhatsApp Cloud numbers used only for the Omni-Channel Inbox.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from ..models import IntegrationAccount, WhatsAppAccount, WhatsAppInboxNumber
from .whatsapp_coexistence import subscribe_waba_webhooks

logger = logging.getLogger(__name__)


class InboxNumberConflictError(Exception):
    def __init__(self, error_key: str, message: str):
        super().__init__(message)
        self.error_key = error_key
        self.message = message


def inbox_number_ids_for_company(company_id: int) -> set[str]:
    return {
        str(pid)
        for pid in WhatsAppInboxNumber.objects.filter(
            company_id=company_id, status='connected'
        ).values_list('phone_number_id', flat=True)
    }


def crm_phone_number_ids_for_company(company_id: int) -> set[str]:
    return {
        str(pid)
        for pid in WhatsAppAccount.objects.filter(
            company_id=company_id, status='connected'
        ).values_list('phone_number_id', flat=True)
    }


def assert_inbox_phone_not_used_by_crm(company_id: int, phone_number_id: str) -> None:
    pid = str(phone_number_id or '').strip()
    if not pid:
        return
    if WhatsAppAccount.objects.filter(
        company_id=company_id, phone_number_id=pid, status='connected'
    ).exists():
        raise InboxNumberConflictError(
            'whatsapp_inbox_number_in_use_by_crm',
            'This number is already connected as your CRM WhatsApp number. '
            'Choose a different number for the inbox.',
        )


def assert_crm_phone_not_used_by_inbox(company_id: int, phone_number_id: str) -> None:
    pid = str(phone_number_id or '').strip()
    if not pid:
        return
    if WhatsAppInboxNumber.objects.filter(
        company_id=company_id, phone_number_id=pid, status='connected'
    ).exists():
        raise InboxNumberConflictError(
            'whatsapp_number_in_use_by_inbox',
            'This number is reserved for the WhatsApp inbox. '
            'Connect a different number for CRM WhatsApp.',
        )


def disconnect_whatsapp_inbox_for_integration(account: IntegrationAccount) -> int:
    if getattr(account, 'platform', None) != 'whatsapp_inbox':
        return 0
    count = 0
    for row in WhatsAppInboxNumber.objects.filter(integration_account=account):
        row.set_access_token(None)
        row.status = 'disconnected'
        row.integration_account = None
        row.save(update_fields=['access_token', 'status', 'integration_account', 'updated_at'])
        count += 1
    return count


@transaction.atomic
def upsert_inbox_number_from_embedded_signup(
    account: IntegrationAccount,
    access_token: str,
    *,
    waba_id: str,
    phone_number_id: str,
    business_id: Optional[str] = None,
) -> WhatsAppInboxNumber:
    phone_number_id = str(phone_number_id).strip()
    assert_inbox_phone_not_used_by_crm(account.company_id, phone_number_id)

    from ..whatsapp_account_sync import _fetch_phone_profile

    profile = _fetch_phone_profile(access_token, phone_number_id)
    display = profile['display']

    row, _created = WhatsAppInboxNumber.objects.update_or_create(
        phone_number_id=phone_number_id,
        defaults={
            'company': account.company,
            'waba_id': str(waba_id),
            'business_id': (business_id or '').strip() or '',
            'display_phone_number': display or '',
            'status': 'connected',
            'integration_account': account,
            'error_message': None,
        },
    )
    row.set_access_token(access_token)
    row.save()

    meta = dict(account.metadata or {})
    meta['waba_id'] = str(waba_id)
    meta['phone_number_id'] = phone_number_id
    if business_id:
        meta['business_id'] = str(business_id)
    account.metadata = meta
    if display and (not account.name or account.name.strip().lower() == 'whatsapp'):
        account.name = display
    account.save(update_fields=['metadata', 'name', 'updated_at'])

    if waba_id:
        subscribe_waba_webhooks(access_token, str(waba_id))

    # Only one inbox number per integration account.
    WhatsAppInboxNumber.objects.filter(
        company_id=account.company_id,
        integration_account=account,
        status='connected',
    ).exclude(phone_number_id=phone_number_id).update(
        status='disconnected',
        access_token=None,
    )

    return row


def disconnect_inbox_number(row: WhatsAppInboxNumber) -> WhatsAppInboxNumber:
    row.set_access_token(None)
    row.status = 'disconnected'
    row.save(update_fields=['access_token', 'status', 'updated_at'])
    return row


def serialize_inbox_number(row: WhatsAppInboxNumber) -> dict:
    return {
        'id': row.id,
        'phone_number_id': row.phone_number_id,
        'display_phone_number': row.display_phone_number,
        'waba_id': row.waba_id,
        'status': row.status,
        'calling_enabled': bool(row.calling_enabled),
        'error_message': row.error_message,
        'last_webhook_at': row.last_webhook_at,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }
