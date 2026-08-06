"""
Create CRM leads from external custom forms (Lead API) and Mujeb mini apps.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.db import IntegrityError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from integrations.models import IntegrationAccount, IntegrationLog, IntegrationPlatform
from integrations.policy import get_effective_integration_policy, get_plan_integration_access
from settings.models import SystemSettings

logger = logging.getLogger(__name__)

LEAD_API_PLATFORM = "api"
MUJEB_PLATFORM = "mujeb"

_SOURCE_LABELS = {
    "api": "Custom Lead API",
    "mujeb": "Mujeb",
}

_LOG_ACTIONS = {
    "api": "api_lead_received",
    "mujeb": "mujeb_lead_received",
}


def integration_gate(company, platform: str = LEAD_API_PLATFORM) -> dict[str, Any]:
    plan_gate = get_plan_integration_access(company, platform)
    if not plan_gate["enabled"]:
        return {
            "enabled": False,
            "message": plan_gate["message"],
            "scope": "plan",
        }
    return get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform=platform,
    )


def get_or_create_lead_api_account(company) -> IntegrationAccount:
    return _get_or_create_inbound_account(
        company=company,
        platform=IntegrationPlatform.API,
        external_account_id=f"lead_api_{company.id}",
        name="Custom Lead API",
    )


def get_or_create_mujeb_account(company) -> IntegrationAccount:
    return _get_or_create_inbound_account(
        company=company,
        platform=IntegrationPlatform.MUJEB,
        external_account_id=f"mujeb_{company.id}",
        name="Mujeb",
    )


def _get_or_create_inbound_account(
    *,
    company,
    platform: str,
    external_account_id: str,
    name: str,
) -> IntegrationAccount:
    account, _ = IntegrationAccount.objects.get_or_create(
        company=company,
        platform=platform,
        external_account_id=external_account_id,
        defaults={
            "name": name,
            "status": "connected",
        },
    )
    metadata = account.metadata if isinstance(account.metadata, dict) else {}
    metadata["last_received_at"] = timezone.now().isoformat()
    account.metadata = metadata
    account.status = "connected"
    account.error_message = None
    account.last_sync_at = timezone.now()
    account.save(update_fields=["metadata", "status", "error_message", "last_sync_at"])
    return account


def find_existing_by_external_id(company, external_id: str):
    from crm.models import Client

    if not external_id:
        return None
    return Client.objects.filter(
        company=company,
        external_lead_id=external_id,
    ).first()


def check_inbound_lead_exists(
    company,
    *,
    phone: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    """
    Read-only lookup: does a lead already exist for this company by external_id or phone?
    Same identity order as create_inbound_lead (external_id first, then phone).
    """
    external_id = (external_id or "").strip() or None
    phone = (phone or "").strip() or None

    if external_id:
        existing = find_existing_by_external_id(company, external_id)
        if existing:
            return {
                "exists": True,
                "matched_by": "external_id",
                "client_id": existing.id,
                "patient_file_number": existing.patient_file_number,
            }

    if phone:
        from integrations.services.phone_match import find_client_by_phone

        existing_by_phone = find_client_by_phone(company, phone)
        if existing_by_phone:
            return {
                "exists": True,
                "matched_by": "phone",
                "client_id": existing_by_phone.id,
                "patient_file_number": existing_by_phone.patient_file_number,
            }

    return {"exists": False}


def _build_notes(*, notes: str | None, email: str | None, custom_fields: dict | None) -> str | None:
    parts: list[str] = []
    if notes and str(notes).strip():
        parts.append(str(notes).strip())
    if email and str(email).strip():
        parts.append(f"Email: {email.strip()}")
    if custom_fields:
        try:
            parts.append("Custom fields: " + json.dumps(custom_fields, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            parts.append(f"Custom fields: {custom_fields}")
    return "\n".join(parts) if parts else None


def _default_lead_status_id(company) -> int | None:
    from crm.lead_defaults import get_default_lead_status_id

    return get_default_lead_status_id(company)


def notify_owner_new_lead(company, client) -> None:
    """Notify company owner that a new lead arrived (Lead API / Meta / TikTok / Mujeb)."""
    owner = getattr(company, "owner", None)
    if not owner or not client:
        return
    try:
        from notifications.models import NotificationType
        from notifications.services import NotificationService

        account = getattr(client, "integration_account", None)
        if account is not None:
            added_by = (
                account.get_platform_display()
                if hasattr(account, "get_platform_display")
                else (getattr(account, "name", None) or "API")
            )
        else:
            added_by = (getattr(client, "source", None) or "API").strip() or "API"

        NotificationService.send_notification(
            user=owner,
            notification_type=NotificationType.NEW_LEAD,
            data={
                "lead_id": client.id,
                "lead_name": client.name,
                "campaign_name": client.campaign.name if client.campaign_id else "",
                "added_by": added_by,
            },
            sender_role=None,
        )
    except Exception:
        logger.exception("Failed to notify owner of new lead for client_id=%s", client.id)


def create_inbound_lead(
    *,
    company,
    account: IntegrationAccount,
    payload: dict[str, Any],
    source: str = "api",
    platform_gate: str = LEAD_API_PLATFORM,
) -> tuple[dict, bool]:
    """
    Create a Client from validated payload.
    Returns (response_data, created) where created is False for idempotent duplicate.
    """
    from crm.models import Client, ClientEvent, ClientPhoneNumber
    from subscriptions.entitlements import require_quota

    source_label = _SOURCE_LABELS.get(source, "Custom Lead API")
    log_action = _LOG_ACTIONS.get(source, "api_lead_received")
    default_name = "Mujeb Lead" if source == "mujeb" else "API Lead"

    external_id = (payload.get("external_id") or "").strip() or None
    phone_preview = (payload.get("phone") or "").strip() or None
    existing_check = check_inbound_lead_exists(
        company, phone=phone_preview, external_id=external_id
    )
    if existing_check.get("exists"):
        from crm.models import Client

        existing = Client.objects.filter(
            company=company, id=existing_check["client_id"]
        ).first()
        return (
            {
                "client_id": existing_check["client_id"],
                "patient_file_number": existing_check.get("patient_file_number"),
                "created_at": (
                    existing.created_at.isoformat()
                    if existing and existing.created_at
                    else None
                ),
                "duplicate": True,
            },
            False,
        )

    gate = integration_gate(company, platform_gate)
    if not gate["enabled"]:
        raise DRFValidationError(
            detail={
                "code": "integration_disabled",
                "message": gate.get("message") or f"{source_label} is disabled for this company.",
            },
            code=403,
        )

    current_clients = Client.objects.filter(company=company).count()
    try:
        require_quota(
            company,
            "max_clients",
            current_count=current_clients,
            requested_delta=1,
            message="Lead limit reached for this company plan.",
            error_key="plan_quota_max_clients_exceeded",
        )
    except DRFValidationError:
        raise

    name = (payload.get("name") or "").strip() or default_name
    phone = (payload.get("phone") or "").strip() or None
    priority = payload.get("priority") or "medium"
    lead_type = payload.get("type") or "fresh"
    notes = _build_notes(
        notes=payload.get("notes"),
        email=payload.get("email"),
        custom_fields=payload.get("custom_fields"),
    )

    status_id = payload.get("status_id")
    if not status_id:
        status_id = _default_lead_status_id(company)

    from django.db import transaction

    try:
        with transaction.atomic():
            client = Client.objects.create(
                name=name,
                priority=priority,
                type=lead_type,
                company=company,
                source=source,
                integration_account=account,
                external_lead_id=external_id,
                phone_number=phone,
                notes=notes,
                communication_way_id=payload.get("communication_way_id"),
                status_id=status_id,
                campaign_id=payload.get("campaign_id"),
                created_by=None,
            )
            if phone:
                ClientPhoneNumber.objects.create(
                    client=client,
                    phone_number=phone,
                    phone_type="mobile",
                    is_primary=True,
                )
    except IntegrityError:
        if external_id:
            existing = find_existing_by_external_id(company, external_id)
            if existing:
                return (
                    {
                        "client_id": existing.id,
                        "patient_file_number": existing.patient_file_number,
                        "created_at": existing.created_at.isoformat() if existing.created_at else None,
                        "duplicate": True,
                    },
                    False,
                )
        if phone:
            from integrations.services.phone_match import find_client_by_phone

            existing_by_phone = find_client_by_phone(company, phone)
            if existing_by_phone:
                return (
                    {
                        "client_id": existing_by_phone.id,
                        "patient_file_number": existing_by_phone.patient_file_number,
                        "created_at": (
                            existing_by_phone.created_at.isoformat()
                            if existing_by_phone.created_at
                            else None
                        ),
                        "duplicate": True,
                    },
                    False,
                )
        raise

    event_notes = f"Lead from {source_label}"
    if payload.get("email"):
        event_notes += f". Email: {payload['email']}"
    ClientEvent.objects.create(
        client=client,
        event_type="created",
        new_value=source_label,
        notes=event_notes,
    )

    IntegrationLog.objects.create(
        account=account,
        action=log_action,
        status="success",
        message=f"Lead created: {name}",
        response_data={
            "client_id": client.id,
            "external_id": external_id,
            "name": name,
            "phone": phone,
        },
    )

    notify_owner_new_lead(company, client)

    return (
        {
            "client_id": client.id,
            "patient_file_number": client.patient_file_number,
            "created_at": client.created_at.isoformat() if client.created_at else None,
            "duplicate": False,
        },
        True,
    )
