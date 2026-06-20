"""Cleanup softphone state when a user or extension is offboarded."""

from __future__ import annotations

import logging

from integrations.models import UserPbxExtension, UserSoftphoneDevice

logger = logging.getLogger(__name__)


def delete_softphone_devices_for_user(user) -> int:
    """Remove registered push tokens so stale devices stop receiving VoIP wake-up."""
    deleted, _ = UserSoftphoneDevice.objects.filter(user=user).delete()
    if deleted:
        logger.info(
            "softphone_offboard devices_deleted user_id=%s count=%s",
            user.id,
            deleted,
        )
    return deleted


def clear_extension_sip_password(mapping: UserPbxExtension) -> None:
    """Clear encrypted SIP password server-side (admin must update PBX separately)."""
    if not mapping.sip_password:
        return
    mapping.sip_password = None
    mapping.save(update_fields=["sip_password", "updated_at"])
    logger.info(
        "softphone_offboard sip_password_cleared user_id=%s extension=%s",
        mapping.user_id,
        mapping.extension,
    )


def offboard_softphone_user(
    user,
    *,
    clear_sip_password: bool = False,
    mapping: UserPbxExtension | None = None,
) -> None:
    """Delete device tokens and optionally clear the extension SIP password."""
    delete_softphone_devices_for_user(user)
    if clear_sip_password:
        ext = mapping
        if ext is None:
            try:
                ext = user.pbx_extension
            except UserPbxExtension.DoesNotExist:
                ext = None
        if ext is not None:
            clear_extension_sip_password(ext)
