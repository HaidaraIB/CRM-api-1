"""
Resolve WhatsAppAccount rows for outbound messaging.

The UI shows IntegrationAccount (platform=whatsapp) as "Connected", but send/webhook
paths use WhatsAppAccount (phone_number_id + token). Rows are created during OAuth
when Meta returns WABA/phone data — if that step fails, IntegrationAccount can still
look connected while sends fail. This module syncs on demand.
"""
import logging
from typing import Optional

import requests

from .models import IntegrationAccount, WhatsAppAccount
from .oauth_utils import get_oauth_handler, META_GRAPH_API_BASE_URL

logger = logging.getLogger(__name__)


def upsert_whatsapp_account_from_embedded_signup(
    account: IntegrationAccount,
    access_token: str,
    *,
    waba_id: str,
    phone_number_id: str,
    business_id: Optional[str] = None,
) -> WhatsAppAccount:
    """
    Create/update WhatsAppAccount using IDs returned by Meta Embedded Signup (WA_EMBEDDED_SIGNUP).
    Required for Meta-provided 555 / display-name-only numbers where Graph list APIs may lag.
    """
    display = None
    verified_name = None
    try:
        resp = requests.get(
            f'{META_GRAPH_API_BASE_URL}/{phone_number_id}',
            params={
                'access_token': access_token,
                'fields': 'display_phone_number,verified_name,name_status',
            },
            timeout=10,
        )
        if resp.status_code == 200:
            j = resp.json()
            display = (j.get('display_phone_number') or '').strip() or None
            verified_name = (j.get('verified_name') or '').strip() or None
    except Exception as e:
        logger.debug('Could not fetch phone_number fields for %s: %s', phone_number_id, e)

    wa_account, _created = WhatsAppAccount.objects.update_or_create(
        phone_number_id=str(phone_number_id),
        defaults={
            'company': account.company,
            'waba_id': str(waba_id),
            'business_id': (business_id or '').strip() or None,
            'display_phone_number': display,
            'status': 'connected',
            'integration_account': account,
        },
    )
    wa_account.set_access_token(access_token)
    wa_account.save()

    meta = dict(account.metadata or {})
    meta['waba_id'] = str(waba_id)
    meta['phone_number_id'] = str(phone_number_id)
    if business_id:
        meta['business_id'] = str(business_id)
    if verified_name:
        meta['verified_name'] = verified_name
    account.metadata = meta
    if display and (not account.name or account.name.strip().lower() == 'whatsapp'):
        account.name = display

    return wa_account


def sync_whatsapp_accounts_from_integration(
    account: IntegrationAccount,
    access_token: Optional[str] = None,
) -> int:
    """
    Fetch WABA + phone numbers from Meta and upsert WhatsAppAccount rows.
    Returns the number of phone numbers synced.
    """
    if account.platform != 'whatsapp':
        return 0
    token = (access_token or '').strip() or account.get_access_token()
    if not token:
        return 0
    wa_handler = get_oauth_handler('whatsapp')
    if not hasattr(wa_handler, 'get_waba_and_phone_numbers'):
        return 0
    try:
        waba_list = wa_handler.get_waba_and_phone_numbers(token)
    except Exception as e:
        logger.warning(
            "get_waba_and_phone_numbers failed for integration account %s: %s",
            account.id,
            e,
        )
        return 0

    if not waba_list:
        logger.warning(
            "get_waba_and_phone_numbers returned no WABAs for integration account %s",
            account.id,
        )

    synced = 0
    first_display = None
    for item in waba_list:
        waba_id = item.get('waba_id')
        business_id = item.get('business_id')
        for ph in item.get('phone_numbers') or []:
            phone_number_id = ph.get('id')
            if not phone_number_id:
                continue
            display = (ph.get('display_phone_number') or '').strip()
            wa_account, _created = WhatsAppAccount.objects.update_or_create(
                phone_number_id=str(phone_number_id),
                defaults={
                    'company': account.company,
                    'waba_id': str(waba_id or ''),
                    'business_id': business_id or '',
                    'display_phone_number': display or None,
                    'status': 'connected',
                    'integration_account': account,
                },
            )
            wa_account.set_access_token(token)
            wa_account.save()
            synced += 1
            if first_display is None and display:
                first_display = display

    if waba_list and synced:
        meta = dict(account.metadata or {})
        first_waba = waba_list[0]
        first_phones = first_waba.get('phone_numbers') or []
        if first_waba.get('waba_id'):
            meta['waba_id'] = first_waba.get('waba_id')
        if first_phones and first_phones[0].get('id'):
            meta['phone_number_id'] = first_phones[0].get('id')
        account.metadata = meta
        if first_display and (
            not account.name or account.name.strip().lower() == 'whatsapp'
        ):
            account.name = first_display
        account.save(update_fields=['metadata', 'name', 'updated_at'])

    return synced


def _whatsapp_account_from_integration_metadata(
    account: IntegrationAccount,
) -> Optional[WhatsAppAccount]:
    meta = account.metadata or {}
    pid = meta.get('phone_number_id')
    waba_id = meta.get('waba_id')
    if not pid or not waba_id:
        return None
    token = account.get_access_token()
    wa_account, _ = WhatsAppAccount.objects.update_or_create(
        phone_number_id=str(pid),
        defaults={
            'company': account.company,
            'waba_id': str(waba_id),
            'status': 'connected',
            'integration_account': account,
        },
    )
    if token:
        wa_account.set_access_token(token)
        wa_account.save()
    return wa_account


def get_connected_whatsapp_account(company, phone_number_id=None) -> Optional[WhatsAppAccount]:
    """
    Return a connected WhatsAppAccount with a usable access token for Graph API sends.
    Attempts lazy sync from connected IntegrationAccount rows when needed.
    """
    pid_filter = str(phone_number_id).strip() if phone_number_id else None

    def _query():
        qs = WhatsAppAccount.objects.filter(company=company, status='connected')
        if pid_filter:
            qs = qs.filter(phone_number_id=pid_filter)
        return qs.first()

    wa = _query()
    if wa:
        if wa.get_access_token():
            return wa
        if wa.integration_account_id:
            tok = wa.integration_account.get_access_token()
            if tok:
                wa.set_access_token(tok)
                wa.save(update_fields=['access_token', 'updated_at'])
                return wa

    integration_accounts = list(
        IntegrationAccount.objects.filter(
            company=company,
            platform='whatsapp',
            status='connected',
        ).order_by('-updated_at')
    )
    if not integration_accounts:
        return None

    for acc in integration_accounts:
        sync_whatsapp_accounts_from_integration(acc)

    wa = _query()
    if wa and wa.get_access_token():
        return wa

    for acc in integration_accounts:
        wa = _whatsapp_account_from_integration_metadata(acc)
        if not wa:
            continue
        if pid_filter and str(wa.phone_number_id) != pid_filter:
            continue
        if wa.get_access_token():
            return wa

    return _query()


def has_connected_whatsapp_integration(company) -> bool:
    return IntegrationAccount.objects.filter(
        company=company,
        platform='whatsapp',
        status='connected',
    ).exists()


def resolve_whatsapp_account_for_api(company, phone_number_id=None):
    """
    Return (WhatsAppAccount, None) or (None, error_code) for outbound Graph API calls.
    """
    wa = get_connected_whatsapp_account(company, phone_number_id)
    if wa:
        return wa, None
    if has_connected_whatsapp_integration(company):
        return None, 'whatsapp_phone_numbers_not_synced'
    return None, 'no_connected_whatsapp_number'
