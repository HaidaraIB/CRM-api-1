"""
Resolve a WhatsApp Cloud API phone_number_id to sender credentials.

One sender type. CRM vs inbox is ``WhatsAppAccount.purpose``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from integrations.models import WhatsAppAccount, WhatsAppCall, WhatsAppPurpose


class WhatsAppCallingConfigLike(Protocol):
    calling_enabled: bool
    call_hours_enabled: bool
    call_hours_timezone: str
    call_hours_weekly: dict
    out_of_hours_message: str


@dataclass(frozen=True)
class AccountSender:
    account: WhatsAppAccount

    @property
    def kind(self) -> str:
        return self.account.purpose

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

    def link_call(self, call: WhatsAppCall, *, peer_phone: str, peer_name: str) -> list[str]:
        if self.kind == WhatsAppPurpose.INBOX:
            return self._link_inbox_call(call, peer_phone=peer_phone)
        return self._link_crm_call(call, peer_phone=peer_phone)

    def _link_crm_call(self, call: WhatsAppCall, *, peer_phone: str) -> list[str]:
        from integrations.services.whatsapp_client import ensure_client_for_whatsapp_phone

        if call.client_id:
            return []
        phone = (peer_phone or call.peer_phone or "").strip()
        if not phone:
            return []
        client = ensure_client_for_whatsapp_phone(
            self.account.company,
            phone,
            integration_account=self.account.integration_account,
        )
        if not client:
            return []
        call.client = client
        return ["client"]

    def _link_inbox_call(self, call: WhatsAppCall, *, peer_phone: str) -> list[str]:
        from integrations.services.whatsapp_inbox_ingest import (
            get_or_create_contact,
            get_or_create_conversation,
        )

        if call.social_conversation_id:
            return []
        phone = (peer_phone or call.peer_phone or "").strip()
        if not phone:
            return []
        contact = get_or_create_contact(self.account, phone)
        if not contact.name:
            contact.name = phone
            contact.save(update_fields=["name", "updated_at"])
        conversation = get_or_create_conversation(self.account, contact)
        call.social_conversation = conversation
        updates = ["social_conversation"]
        if call.client_id is None and conversation.client_id:
            call.client = conversation.client
            updates.append("client")
        return updates


ResolvedSender = AccountSender


def resolve_whatsapp_sender(phone_number_id: str) -> Optional[AccountSender]:
    pid = str(phone_number_id or "").strip()
    if not pid:
        return None
    account = (
        WhatsAppAccount.objects.select_related("company")
        .filter(phone_number_id=pid, status="connected")
        .first()
    )
    if account:
        return AccountSender(account)
    return None


def sender_for_call(call: WhatsAppCall) -> Optional[AccountSender]:
    if call.whatsapp_account_id:
        return AccountSender(call.whatsapp_account)
    return None


def is_seed_sender(sender: AccountSender) -> bool:
    from integrations.services.whatsapp_calling import is_seed_whatsapp_account

    return is_seed_whatsapp_account(sender.account)
