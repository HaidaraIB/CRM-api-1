"""
Resolve a WhatsApp Cloud API phone_number_id to CRM or Inbox sender credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol, Union

from integrations.models import WhatsAppAccount, WhatsAppCall, WhatsAppInboxNumber

from .whatsapp_inbox_ingest import get_or_create_contact, get_or_create_conversation


class WhatsAppCallingConfigLike(Protocol):
    calling_enabled: bool
    call_hours_enabled: bool
    call_hours_timezone: str
    call_hours_weekly: dict
    out_of_hours_message: str


class WhatsAppSender(Protocol):
    kind: Literal["crm", "inbox"]
    company: object
    phone_number_id: str
    pk: int

    def get_access_token(self) -> str | None: ...

    @property
    def calling_config(self) -> WhatsAppCallingConfigLike: ...

    def link_call(self, call: WhatsAppCall, *, peer_phone: str, peer_name: str) -> list[str]:
        """Return model field names updated on call (e.g. client, social_conversation)."""
        ...


@dataclass(frozen=True)
class CrmSender:
    account: WhatsAppAccount

    kind: Literal["crm", "inbox"] = "crm"

    @property
    def company(self):
        return self.account.company

    @property
    def phone_number_id(self) -> str:
        return self.account.phone_number_id

    @property
    def pk(self) -> int:
        return self.account.pk

    def get_access_token(self) -> str | None:
        return self.account.get_access_token()

    @property
    def calling_config(self) -> WhatsAppAccount:
        return self.account

    @property
    def whatsapp_account(self) -> WhatsAppAccount:
        return self.account

    def link_call(self, call: WhatsAppCall, *, peer_phone: str, peer_name: str) -> list[str]:
        from integrations.services.whatsapp_client import ensure_client_for_whatsapp_phone

        updates: list[str] = []
        if call.client_id:
            return updates
        phone = (peer_phone or call.peer_phone or "").strip()
        if not phone:
            return updates
        client = ensure_client_for_whatsapp_phone(
            self.account.company,
            phone,
            integration_account=self.account.integration_account,
        )
        if client:
            call.client = client
            updates.append("client")
        return updates


@dataclass(frozen=True)
class InboxSender:
    inbox_number: WhatsAppInboxNumber

    kind: Literal["crm", "inbox"] = "inbox"

    @property
    def company(self):
        return self.inbox_number.company

    @property
    def phone_number_id(self) -> str:
        return self.inbox_number.phone_number_id

    @property
    def pk(self) -> int:
        return self.inbox_number.pk

    def get_access_token(self) -> str | None:
        return self.inbox_number.get_access_token()

    @property
    def calling_config(self) -> WhatsAppInboxNumber:
        return self.inbox_number

    @property
    def wa_inbox_number(self) -> WhatsAppInboxNumber:
        return self.inbox_number

    def link_call(self, call: WhatsAppCall, *, peer_phone: str, peer_name: str) -> list[str]:
        updates: list[str] = []
        if call.social_conversation_id:
            return updates
        phone = (peer_phone or call.peer_phone or "").strip()
        if not phone:
            return updates
        contact = get_or_create_contact(self.inbox_number, phone)
        if not contact.name:
            contact.name = phone
            contact.save(update_fields=["name", "updated_at"])
        conversation = get_or_create_conversation(self.inbox_number, contact)
        call.social_conversation = conversation
        updates.append("social_conversation")
        if call.client_id is None and conversation.client_id:
            call.client = conversation.client
            updates.append("client")
        return updates


ResolvedSender = Union[CrmSender, InboxSender]


def resolve_whatsapp_sender(phone_number_id: str) -> Optional[ResolvedSender]:
    pid = str(phone_number_id or "").strip()
    if not pid:
        return None
    inbox = (
        WhatsAppInboxNumber.objects.select_related("company")
        .filter(phone_number_id=pid, status="connected")
        .first()
    )
    if inbox:
        return InboxSender(inbox)
    account = (
        WhatsAppAccount.objects.select_related("company")
        .filter(phone_number_id=pid, status="connected")
        .first()
    )
    if account:
        return CrmSender(account)
    return None


def sender_for_call(call: WhatsAppCall) -> Optional[ResolvedSender]:
    if call.wa_inbox_number_id:
        return InboxSender(call.wa_inbox_number)
    if call.whatsapp_account_id:
        return CrmSender(call.whatsapp_account)
    return None


def is_seed_sender(sender: ResolvedSender) -> bool:
    if isinstance(sender, CrmSender):
        from integrations.services.whatsapp_calling import is_seed_whatsapp_account

        return is_seed_whatsapp_account(sender.account)
    pid = (sender.phone_number_id or "").strip()
    token = (sender.get_access_token() or "").strip()
    return pid.startswith("seed_") or token.startswith("seed-fake")
