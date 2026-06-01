"""
Create CRM leads from external custom forms (Lead API).
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


def integration_gate(company) -> dict[str, Any]:
    plan_gate = get_plan_integration_access(company, LEAD_API_PLATFORM)
    if not plan_gate["enabled"]:
        return {
            "enabled": False,
            "message": plan_gate["message"],
            "scope": "plan",
        }
    return get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform=LEAD_API_PLATFORM,
    )


def get_or_create_lead_api_account(company) -> IntegrationAccount:
    account, _ = IntegrationAccount.objects.get_or_create(
        company=company,
        platform=IntegrationPlatform.API,
        external_account_id=f"lead_api_{company.id}",
        defaults={
            "name": "Custom Lead API",
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


def _notify_owner_new_lead(company, client) -> None:
    owner = getattr(company, "owner", None)
    if not owner or not client:
        return
    try:
        from notifications.models import NotificationType
        from notifications.services import NotificationService

        NotificationService.send_notification(
            user=owner,
            notification_type=NotificationType.NEW_LEAD,
            data={
                "lead_id": client.id,
                "lead_name": client.name,
                "campaign_name": client.campaign.name if client.campaign_id else "",
            },
            sender_role=None,
        )
    except Exception:
        logger.exception("Lead API: failed to notify owner for client_id=%s", client.id)


def create_inbound_lead(*, company, account: IntegrationAccount, payload: dict[str, Any]) -> tuple[dict, bool]:
    """
    Create a Client from validated payload.
    Returns (response_data, created) where created is False for idempotent duplicate.
    """
    from crm.models import Client, ClientEvent, ClientPhoneNumber
    from subscriptions.entitlements import require_quota

    external_id = (payload.get("external_id") or "").strip() or None
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

    gate = integration_gate(company)
    if not gate["enabled"]:
        raise DRFValidationError(
            detail={
                "code": "integration_disabled",
                "message": gate.get("message") or "Lead API is disabled for this company.",
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

    name = (payload.get("name") or "").strip() or "API Lead"
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

    try:
        client = Client.objects.create(
            name=name,
            priority=priority,
            type=lead_type,
            company=company,
            source="api",
            integration_account=account,
            external_lead_id=external_id,
            phone_number=phone,
            notes=notes,
            communication_way_id=payload.get("communication_way_id"),
            status_id=status_id,
            campaign_id=payload.get("campaign_id"),
            created_by=None,
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
        raise

    if phone:
        ClientPhoneNumber.objects.create(
            client=client,
            phone_number=phone,
            phone_type="mobile",
            is_primary=True,
        )

    event_notes = "Lead from Custom Lead API"
    if payload.get("email"):
        event_notes += f". Email: {payload['email']}"
    ClientEvent.objects.create(
        client=client,
        event_type="created",
        new_value="Custom Lead API",
        notes=event_notes,
    )

    IntegrationLog.objects.create(
        account=account,
        action="api_lead_received",
        status="success",
        message=f"Lead created: {name}",
        response_data={
            "client_id": client.id,
            "external_id": external_id,
            "name": name,
            "phone": phone,
        },
    )

    _notify_owner_new_lead(company, client)

    return (
        {
            "client_id": client.id,
            "patient_file_number": client.patient_file_number,
            "created_at": client.created_at.isoformat() if client.created_at else None,
            "duplicate": False,
        },
        True,
    )
