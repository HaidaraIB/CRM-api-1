"""
Resolve WhatsAppAccount rows for outbound messaging.

The UI shows IntegrationAccount (platform=whatsapp) as "Connected", but send/webhook
paths use WhatsAppAccount (phone_number_id + token).

Product rule: each WhatsApp IntegrationAccount has at most one connected phone —
the number chosen in Meta Embedded Signup (stored as metadata.phone_number_id).
Graph may list other WABA phones (e.g. leftover Meta 555 test numbers); those must
not stay connected or become the default sender.
"""
import logging
import re
from typing import Optional

import requests
from django.db.models import Q

from .models import IntegrationAccount, WhatsAppAccount
from .oauth_utils import get_oauth_handler, META_GRAPH_API_BASE_URL

logger = logging.getLogger(__name__)


def disconnect_whatsapp_accounts_for_integration(account: IntegrationAccount) -> int:
    """
    Mark WhatsApp phone rows as disconnected and clear their tokens.

    Updates rows linked to this IntegrationAccount, plus company orphans still
    marked connected with no integration FK (left behind by DELETE + SET_NULL).
    """
    if getattr(account, 'platform', None) != 'whatsapp':
        return 0

    qs = WhatsAppAccount.objects.filter(company_id=account.company_id).filter(
        Q(integration_account=account)
        | Q(integration_account__isnull=True, status='connected')
    )
    count = 0
    for wa in qs:
        wa.set_access_token(None)
        wa.status = 'disconnected'
        wa.integration_account = None
        wa.save(update_fields=['access_token', 'status', 'integration_account', 'updated_at'])
        count += 1
    if count:
        logger.info(
            'Disconnected %s WhatsAppAccount row(s) for integration %s (company=%s)',
            count,
            account.id,
            account.company_id,
        )
    return count


def _digits_only(value: Optional[str]) -> str:
    return re.sub(r'\D', '', value or '')


def is_meta_provided_test_number(
    *,
    display_phone_number: Optional[str] = None,
    phone_number_id: Optional[str] = None,
) -> bool:
    """Meta Embedded Signup test / display-name-only lines (typically +1 555-…)."""
    pid = str(phone_number_id or '')
    if pid.startswith('seed_'):
        return True
    digits = _digits_only(display_phone_number)
    # E.164 US test numbers Meta issues: +1 555-xxx-xxxx → 1555…
    if digits.startswith('1555') and len(digits) >= 11:
        return True
    if digits.startswith('555') and len(digits) >= 10:
        return True
    return False


def _apply_display_name_metadata(meta: dict, *, name_status=None, verified_name=None) -> dict:
    """Write ChatsPage display-name banner keys from Meta name_status."""
    out = dict(meta or {})
    status = (name_status or '').strip().upper() or None
    if status:
        out['display_name_status'] = status
        out['name_status'] = status
        out['display_name_approved'] = status in (
            'APPROVED',
            'AVAILABLE_WITHOUT_REVIEW',
        )
    if verified_name:
        out['verified_name'] = verified_name
    return out


def disconnect_extra_whatsapp_phones_for_integration(
    account: IntegrationAccount,
    keep_phone_number_id: str,
) -> int:
    """
    Ensure only keep_phone_number_id stays connected for this integration.
    Other WhatsAppAccount rows linked to the same IntegrationAccount are disconnected.
    """
    keep = str(keep_phone_number_id or '').strip()
    if not keep or getattr(account, 'platform', None) != 'whatsapp':
        return 0
    extras = WhatsAppAccount.objects.filter(
        company_id=account.company_id,
        integration_account=account,
        status='connected',
    ).exclude(phone_number_id=keep)
    count = 0
    for wa in extras:
        wa.set_access_token(None)
        wa.status = 'disconnected'
        wa.save(update_fields=['access_token', 'status', 'updated_at'])
        count += 1
    if count:
        logger.info(
            'Disconnected %s extra WhatsApp phone(s) for integration %s; kept phone_number_id=%s',
            count,
            account.id,
            keep,
        )
    return count


def _fetch_phone_profile(access_token: str, phone_number_id: str) -> dict:
    """Graph fields for a single phone_number_id."""
    out = {
        'display': None,
        'verified_name': None,
        'name_status': None,
    }
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
            out['display'] = (j.get('display_phone_number') or '').strip() or None
            out['verified_name'] = (j.get('verified_name') or '').strip() or None
            out['name_status'] = (j.get('name_status') or '').strip() or None
    except Exception as e:
        logger.debug('Could not fetch phone_number fields for %s: %s', phone_number_id, e)
    return out


def _find_phone_in_waba_list(waba_list: list, phone_number_id: str):
    """Return (waba_id, business_id, phone_dict) or (None, None, None)."""
    want = str(phone_number_id)
    for item in waba_list or []:
        for ph in item.get('phone_numbers') or []:
            if str(ph.get('id') or '') == want:
                return item.get('waba_id'), item.get('business_id'), ph
    return None, None, None


def _pick_bootstrap_phone(waba_list: list):
    """
    When metadata has no phone_number_id yet, choose one production-like number.
    Prefer non-Meta-555 numbers; otherwise first Graph phone.
    """
    candidates = []
    for item in waba_list or []:
        for ph in item.get('phone_numbers') or []:
            pid = ph.get('id')
            if not pid:
                continue
            candidates.append((item, ph))
    if not candidates:
        return None, None, None
    for item, ph in candidates:
        display = (ph.get('display_phone_number') or '').strip()
        if not is_meta_provided_test_number(
            display_phone_number=display, phone_number_id=ph.get('id')
        ):
            return item.get('waba_id'), item.get('business_id'), ph
    item, ph = candidates[0]
    return item.get('waba_id'), item.get('business_id'), ph


def upsert_whatsapp_account_from_embedded_signup(
    account: IntegrationAccount,
    access_token: str,
    *,
    waba_id: str,
    phone_number_id: str,
    business_id: Optional[str] = None,
) -> WhatsAppAccount:
    """
    Create/update the single WhatsAppAccount for the phone chosen in Embedded Signup.
    Disconnects any other connected phones on this integration.
    """
    phone_number_id = str(phone_number_id).strip()
    profile = _fetch_phone_profile(access_token, phone_number_id)
    display = profile['display']
    verified_name = profile['verified_name']
    name_status = profile['name_status']

    wa_account, _created = WhatsAppAccount.objects.update_or_create(
        phone_number_id=phone_number_id,
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
    meta['phone_number_id'] = phone_number_id
    if business_id:
        meta['business_id'] = str(business_id)
    meta = _apply_display_name_metadata(meta, name_status=name_status, verified_name=verified_name)
    account.metadata = meta
    if display and (not account.name or account.name.strip().lower() == 'whatsapp'):
        account.name = display

    disconnect_extra_whatsapp_phones_for_integration(account, phone_number_id)
    return wa_account


def sync_whatsapp_accounts_from_integration(
    account: IntegrationAccount,
    access_token: Optional[str] = None,
) -> int:
    """
    Refresh the single selected WhatsApp phone for this integration from Meta.

    Uses metadata.phone_number_id (Embedded Signup pick). If missing, bootstraps one
    non-test number from Graph and pins it in metadata. Never keeps multiple phones
    connected on the same IntegrationAccount.
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

    meta = dict(account.metadata or {})
    preferred_pid = str(meta.get('phone_number_id') or '').strip()
    preferred_waba = str(meta.get('waba_id') or '').strip() or None
    preferred_business = str(meta.get('business_id') or '').strip() or None

    waba_id = preferred_waba
    business_id = preferred_business
    ph = None
    if preferred_pid:
        waba_id, business_id, ph = _find_phone_in_waba_list(waba_list, preferred_pid)
        if not ph:
            # Wizard phone may lag in list APIs; still pin it via direct Graph GET.
            waba_id = preferred_waba
            business_id = preferred_business
            ph = {'id': preferred_pid}
    else:
        waba_id, business_id, ph = _pick_bootstrap_phone(waba_list)
        if ph and ph.get('id'):
            preferred_pid = str(ph.get('id'))
            logger.info(
                'Bootstrapped WhatsApp phone_number_id=%s for integration %s (no wizard metadata)',
                preferred_pid,
                account.id,
            )

    if not preferred_pid or not ph:
        return 0

    display = (ph.get('display_phone_number') or '').strip() or None
    name_status = (ph.get('name_status') or '').strip() or None
    verified_name = (ph.get('verified_name') or '').strip() or None
    # Always refresh profile for the pinned number (list payloads may omit name_status).
    profile = _fetch_phone_profile(token, preferred_pid)
    display = profile['display'] or display
    name_status = profile['name_status'] or name_status
    verified_name = profile['verified_name'] or verified_name

    if not waba_id:
        # Keep existing row waba if Graph list didn't include this phone.
        existing = WhatsAppAccount.objects.filter(phone_number_id=preferred_pid).first()
        waba_id = (existing.waba_id if existing else '') or preferred_waba or ''

    wa_account, _created = WhatsAppAccount.objects.update_or_create(
        phone_number_id=preferred_pid,
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

    meta['waba_id'] = str(waba_id or meta.get('waba_id') or '')
    meta['phone_number_id'] = preferred_pid
    if business_id:
        meta['business_id'] = str(business_id)
    meta = _apply_display_name_metadata(meta, name_status=name_status, verified_name=verified_name)
    account.metadata = meta
    if display and (not account.name or account.name.strip().lower() == 'whatsapp'):
        account.name = display
    account.save(update_fields=['metadata', 'name', 'updated_at'])

    disconnect_extra_whatsapp_phones_for_integration(account, preferred_pid)
    return 1


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
    disconnect_extra_whatsapp_phones_for_integration(account, str(pid))
    return wa_account


def _preferred_phone_number_id_for_company(company) -> Optional[str]:
    acc = (
        IntegrationAccount.objects.filter(
            company=company,
            platform='whatsapp',
            status='connected',
        )
        .order_by('-updated_at')
        .first()
    )
    if not acc:
        return None
    pid = str((acc.metadata or {}).get('phone_number_id') or '').strip()
    return pid or None


def get_connected_whatsapp_account(company, phone_number_id=None) -> Optional[WhatsAppAccount]:
    """
    Return a connected WhatsAppAccount with a usable access token for Graph API sends.
    Prefers the Embedded Signup phone in IntegrationAccount.metadata.
    """
    pid_filter = str(phone_number_id).strip() if phone_number_id else None

    def _query():
        qs = WhatsAppAccount.objects.filter(company=company, status='connected')
        if pid_filter:
            return qs.filter(phone_number_id=pid_filter).first()

        preferred = _preferred_phone_number_id_for_company(company)
        if preferred:
            wa = qs.filter(phone_number_id=preferred).first()
            if wa:
                return wa

        # Prefer production numbers over Meta 555 test lines / seed rows.
        for wa in qs.exclude(phone_number_id__startswith='seed_').order_by('-updated_at'):
            if not is_meta_provided_test_number(
                display_phone_number=wa.display_phone_number,
                phone_number_id=wa.phone_number_id,
            ):
                return wa
        real = qs.exclude(phone_number_id__startswith='seed_').order_by('-updated_at').first()
        if real:
            return real
        return qs.order_by('-updated_at').first()

    # No connected IntegrationAccount → clear orphan phone rows left by DELETE + SET_NULL
    # so outbound send cannot succeed while the UI shows Disconnected.
    if not has_connected_whatsapp_integration(company):
        orphans = list(
            WhatsAppAccount.objects.filter(company=company, status='connected')
        )
        for wa in orphans:
            wa.set_access_token(None)
            wa.status = 'disconnected'
            wa.integration_account = None
            wa.save(update_fields=['access_token', 'status', 'integration_account', 'updated_at'])
        if orphans:
            logger.info(
                'Cleared %s orphan connected WhatsAppAccount row(s) for company=%s (no connected integration)',
                len(orphans),
                getattr(company, 'id', company),
            )
        return None

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
