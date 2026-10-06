"""
Who owns a WhatsApp ``phone_number_id``.

Rule: **one number = one owner**. A Meta phone number is used in exactly one place:

* ``platform`` – the LOOP platform number (signup OTP + admin ↔ company-owner thread)
* ``crm``      – a company's CRM WhatsApp number (``WhatsAppAccount.purpose=crm``)
* ``inbox``    – a company's inbox WhatsApp number (``WhatsAppAccount.purpose=inbox``)

* ``assert_number_available`` enforces the rule whenever a number is connected/synced.
* ``resolve_inbound_owner`` gives the single owner an inbound message belongs to.
* ``find_number_conflicts`` / ``pick_keeper`` are used by
  ``manage.py resolve_whatsapp_number_conflicts`` to clean up numbers that were shared
  before the rule existed. There is no shared-number routing: if a conflict ever
  appears it is logged as an error and treated exactly as the cleanup would resolve it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..models import WhatsAppAccount, WhatsAppPurpose

logger = logging.getLogger(__name__)

OWNER_PLATFORM = 'platform'
OWNER_CRM = 'crm'
OWNER_INBOX = 'inbox'
OWNER_KINDS = (OWNER_PLATFORM, OWNER_CRM, OWNER_INBOX)

# error_key → English fallback (clients see the localized text from the frontend).
CONFLICT_MESSAGES = {
    'whatsapp_number_is_platform_number': (
        "This WhatsApp number is reserved and can't be connected. "
        'Please connect a different number.'
    ),
    'whatsapp_number_in_use_by_other_company': (
        'This WhatsApp number is already connected to another account. '
        'Disconnect it there first, or contact support.'
    ),
    'whatsapp_number_in_use_by_inbox': (
        'This number is already used for your WhatsApp inbox. '
        'Choose a different number for CRM WhatsApp.'
    ),
    'whatsapp_inbox_number_in_use_by_crm': (
        'This number is already used for CRM WhatsApp. '
        'Choose a different number for the inbox.'
    ),
}


class WhatsAppNumberConflictError(Exception):
    def __init__(self, error_key: str, message: Optional[str] = None):
        self.error_key = error_key
        self.message = message or CONFLICT_MESSAGES.get(error_key, error_key)
        super().__init__(self.message)


@dataclass
class NumberOwner:
    kind: str
    company_id: Optional[int] = None
    wa_account: Optional[WhatsAppAccount] = None

    @property
    def inbox_number(self) -> Optional[WhatsAppAccount]:
        if self.kind == OWNER_INBOX:
            return self.wa_account
        return None

    @property
    def row(self):
        return self.wa_account

    @property
    def connected_at(self) -> Optional[datetime]:
        return getattr(self.row, 'created_at', None)

    def describe(self) -> str:
        return self.kind if self.company_id is None else f'{self.kind}(company_id={self.company_id})'


def _pid(value) -> str:
    return str(value or '').strip()


def platform_phone_number_id() -> str:
    from accounts.platform_whatsapp import effective_platform_phone_number_id

    return _pid(effective_platform_phone_number_id())


def owners_for_phone_number_id(phone_number_id) -> list[NumberOwner]:
    """All current owners of this number (zero or one once conflicts are cleaned up)."""
    pid = _pid(phone_number_id)
    if not pid:
        return []
    owners: list[NumberOwner] = []
    if pid == platform_phone_number_id():
        owners.append(NumberOwner(OWNER_PLATFORM))
    wa = (
        WhatsAppAccount.objects.filter(phone_number_id=pid, status='connected')
        .select_related('company', 'integration_account')
        .first()
    )
    if wa:
        kind = OWNER_INBOX if wa.purpose == WhatsAppPurpose.INBOX else OWNER_CRM
        owners.append(NumberOwner(kind, wa.company_id, wa_account=wa))
    return owners


def conflict_key_for(loser: NumberOwner, keeper: NumberOwner) -> str:
    """Error key explaining to ``loser`` why it can't use a number ``keeper`` owns."""
    if keeper.kind == OWNER_PLATFORM:
        return 'whatsapp_number_is_platform_number'
    if keeper.company_id != loser.company_id:
        return 'whatsapp_number_in_use_by_other_company'
    if keeper.kind == OWNER_INBOX:
        return 'whatsapp_number_in_use_by_inbox'
    return 'whatsapp_inbox_number_in_use_by_crm'


def assert_number_available(company_id: int, phone_number_id, kind: str) -> None:
    """
    Raise ``WhatsAppNumberConflictError`` unless ``company_id`` may use this number
    as ``kind`` (``crm`` or ``inbox``). Re-connecting a number this company already
    owns for the same purpose is just a reconnect and is allowed.
    """
    pid = _pid(phone_number_id)
    if not pid:
        return
    me = NumberOwner(kind, company_id)
    for owner in owners_for_phone_number_id(pid):
        if owner.kind == kind and owner.company_id == company_id:
            continue
        raise WhatsAppNumberConflictError(conflict_key_for(me, owner))


def pick_keeper(owners: list[NumberOwner]) -> NumberOwner:
    """
    Which owner keeps a shared number: the platform always; otherwise whoever
    connected it first.
    """
    for owner in owners:
        if owner.kind == OWNER_PLATFORM:
            return owner
    return min(owners, key=lambda o: o.connected_at)


def find_number_conflicts() -> list[tuple[str, list[NumberOwner]]]:
    """Every phone_number_id currently owned by more than one place."""
    pids = set(
        WhatsAppAccount.objects.filter(status='connected').values_list('phone_number_id', flat=True)
    )
    out = []
    for pid in sorted(_pid(p) for p in pids if p):
        owners = owners_for_phone_number_id(pid)
        if len(owners) > 1:
            out.append((pid, owners))
    return out


def resolve_inbound_owner(phone_number_id) -> Optional[NumberOwner]:
    """The single owner an inbound message/status on this number belongs to."""
    owners = owners_for_phone_number_id(phone_number_id)
    if len(owners) <= 1:
        return owners[0] if owners else None
    keeper = pick_keeper(owners)
    logger.error(
        'WhatsApp phone_number_id=%s has several owners %s — routing to %s. '
        'Run: python manage.py resolve_whatsapp_number_conflicts --apply',
        phone_number_id,
        [o.describe() for o in owners],
        keeper.describe(),
    )
    return keeper


# --- platform thread helpers -----------------------------------------------------


def normalize_digits(phone) -> str:
    from accounts.platform_whatsapp import normalize_phone_digits

    return normalize_phone_digits(phone or '')


def owner_company_for_phone(phone):
    """Company whose owner (admin user) has this phone, or None."""
    from accounts.models import Role, User

    digits = normalize_digits(phone)
    if not digits:
        return None
    qs = User.objects.filter(role=Role.ADMIN.value, company__isnull=False).select_related('company')
    for user in qs.iterator(chunk_size=500):
        if normalize_digits(user.phone) == digits and user.company.owner_id == user.id:
            return user.company
    return None
