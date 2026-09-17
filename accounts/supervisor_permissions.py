from __future__ import annotations

from typing import TYPE_CHECKING

from .models import Role, SupervisorPermission, User

if TYPE_CHECKING:
    from companies.models import Company


def ensure_supervisor_permissions_for_company(company: Company) -> int:
    """
    Create missing SupervisorPermission rows for users with role=supervisor.

    Orphaned supervisor users can appear on the Employees list but not in
    Settings → Supervisors until their permission profile exists.
    """
    existing_user_ids = SupervisorPermission.objects.filter(
        user__company=company,
    ).values_list("user_id", flat=True)
    orphans = User.objects.filter(
        company=company,
        role=Role.SUPERVISOR.value,
    ).exclude(id__in=existing_user_ids)
    created = 0
    for user in orphans:
        SupervisorPermission.objects.create(user=user, is_active=True)
        created += 1
    return created
