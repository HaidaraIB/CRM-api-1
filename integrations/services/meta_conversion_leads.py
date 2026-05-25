"""
Meta Conversion Leads (CAPI) — send CRM funnel stage events back to Meta for Lead Ads optimization.
"""
from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from integrations.models import IntegrationAccount, IntegrationLog
from integrations.oauth_utils import MetaOAuth

logger = logging.getLogger(__name__)

LEAD_EVENT_SOURCE = "LOOP CRM"
EVENT_RAW_LEAD = "Raw Lead"
EVENT_QUALIFIED = "Qualified"
EVENT_UNQUALIFIED = "Unqualified"

QUALIFICATION_EVENT_NAMES = {
    "qualified": EVENT_QUALIFIED,
    "unqualified": EVENT_UNQUALIFIED,
}


def _account_metadata(account: IntegrationAccount | None) -> dict:
    if account is None:
        return {}
    meta = account.metadata
    return meta if isinstance(meta, dict) else {}


def get_pixel_id(account: IntegrationAccount | None) -> str | None:
    pixel_id = str(_account_metadata(account).get("pixel_id") or "").strip()
    return pixel_id or None


def conversion_leads_enabled(account: IntegrationAccount | None) -> bool:
    meta = _account_metadata(account)
    if meta.get("conversion_leads_enabled") is False:
        return False
    return bool(get_pixel_id(account))


def resolve_page_access_token(account: IntegrationAccount, page_id: str) -> str | None:
    """Resolve Page access token from integration account metadata or Graph API."""
    metadata = _account_metadata(account)
    page_id_str = str(page_id).strip()
    for page in metadata.get("pages", []) or []:
        pid = page.get("id")
        if pid is not None and str(pid).strip() == page_id_str and page.get("access_token"):
            return page.get("access_token")

    access_token = account.get_access_token()
    if not access_token:
        return None

    meta_oauth = MetaOAuth()
    try:
        fresh_pages = meta_oauth.get_pages(access_token)
        for p in fresh_pages:
            if str(p.get("id", "")).strip() == page_id_str:
                if p.get("access_token"):
                    return p.get("access_token")
                break
    except Exception:
        pass

    try:
        return meta_oauth.get_page_access_token(page_id_str, access_token)
    except Exception:
        return None


def _resolve_access_token_for_client(client) -> str | None:
    account = client.integration_account
    if account is None:
        return None
    metadata = _account_metadata(account)
    page_id = str(metadata.get("selected_page_id") or "").strip()
    if page_id:
        page_token = resolve_page_access_token(account, page_id)
        if page_token:
            return page_token
    return account.get_access_token()


def build_conversion_lead_event(
    *,
    event_name: str,
    leadgen_id: str,
    event_time: int | None = None,
) -> dict[str, Any]:
    ts = event_time if event_time is not None else int(timezone.now().timestamp())
    lead_id_val: int | str = leadgen_id
    if str(leadgen_id).isdigit():
        lead_id_val = int(leadgen_id)
    return {
        "event_name": event_name,
        "event_time": ts,
        "user_data": {"lead_id": lead_id_val},
        "action_source": "system_generated",
        "custom_data": {
            "lead_event_source": LEAD_EVENT_SOURCE,
            "event_source": "crm",
        },
    }


def send_conversion_lead_event(client, event_name: str) -> dict[str, Any]:
    """
    Send a single Conversion Leads stage event to Meta for a Client.
    Returns dict with keys: success, error_key (optional), message (optional), response (optional).
    """
    if client.source != "meta_lead_form":
        return {"success": False, "error_key": "metaQualificationErrorNotMetaLead"}
    if not client.meta_leadgen_id:
        return {"success": False, "error_key": "metaQualificationErrorNoLeadgenId"}
    account = client.integration_account
    if account is None:
        return {"success": False, "error_key": "metaQualificationErrorNoAccount"}
    if not conversion_leads_enabled(account):
        return {"success": False, "error_key": "metaQualificationErrorNoPixelConfigured"}

    pixel_id = get_pixel_id(account)
    access_token = _resolve_access_token_for_client(client)
    if not pixel_id or not access_token:
        return {"success": False, "error_key": "metaQualificationErrorNoToken"}

    event_time = int(timezone.now().timestamp())
    if client.created_at:
        created_ts = int(client.created_at.timestamp())
        if created_ts >= event_time:
            event_time = created_ts + 1

    payload_event = build_conversion_lead_event(
        event_name=event_name,
        leadgen_id=client.meta_leadgen_id,
        event_time=event_time,
    )

    meta_oauth = MetaOAuth()
    try:
        response_data = meta_oauth.send_conversion_leads_events(
            pixel_id, access_token, [payload_event]
        )
        IntegrationLog.objects.create(
            account=account,
            action="meta_conversion_lead_event",
            status="success",
            message=f"Sent Meta Conversion Leads event '{event_name}' for client {client.id}",
            response_data={
                "client_id": client.id,
                "leadgen_id": client.meta_leadgen_id,
                "event_name": event_name,
                "meta_response": response_data,
            },
        )
        return {"success": True, "message": "Event sent", "response": response_data}
    except Exception as exc:
        err_msg = str(exc)
        logger.warning(
            "Meta Conversion Leads event failed client=%s event=%s: %s",
            client.id,
            event_name,
            err_msg,
        )
        IntegrationLog.objects.create(
            account=account,
            action="meta_conversion_lead_event",
            status="error",
            message=f"Failed Meta Conversion Leads event '{event_name}' for client {client.id}",
            error_details=err_msg,
            response_data={
                "client_id": client.id,
                "leadgen_id": client.meta_leadgen_id,
                "event_name": event_name,
            },
        )
        return {"success": False, "error_key": "metaQualificationErrorSendFailed", "message": err_msg}


def send_raw_lead_event(client) -> dict[str, Any]:
    return send_conversion_lead_event(client, EVENT_RAW_LEAD)


def send_qualification_event(client, status: str) -> dict[str, Any]:
    event_name = QUALIFICATION_EVENT_NAMES.get(status)
    if not event_name:
        return {"success": False, "error_key": "metaQualificationErrorUnknownStatus"}
    return send_conversion_lead_event(client, event_name)


def _qualification_error_storage(result: dict[str, Any]) -> str:
    """Persist a translation key when available; otherwise a short fallback key."""
    error_key = result.get("error_key")
    if error_key:
        return str(error_key)
    return "metaQualificationErrorSendFailed"


def apply_qualification_status_change(client, new_status: str | None, previous_status: str | None) -> None:
    """Update client meta qualification fields and send Meta event when status changes to qualified/unqualified."""
    if new_status == previous_status:
        return
    if new_status not in ("qualified", "unqualified"):
        client.meta_qualification_error = None
        client.save(update_fields=["meta_qualification_error"])
        return

    result = send_qualification_event(client, new_status)
    if result.get("success"):
        client.meta_qualification_sent_at = timezone.now()
        client.meta_qualification_error = None
        client.save(update_fields=["meta_qualification_sent_at", "meta_qualification_error"])
    else:
        client.meta_qualification_error = _qualification_error_storage(result)
        client.save(update_fields=["meta_qualification_error"])
