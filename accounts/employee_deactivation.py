"""
Employee / supervisor deactivation with optional lead redistribution.
"""
from django.db import transaction

from accounts.models import Role, SupervisorPermission, User
from crm.models import Client
from crm.services import distribute_clients_to_least_busy
from crm.signals import get_least_busy_employee

DEACTIVATABLE_EMPLOYEE_ROLES = frozenset(
    {
        Role.EMPLOYEE.value,
        Role.DOCTOR.value,
        Role.RECEPTION.value,
        Role.DATA_ENTRY.value,
    }
)

# Roles that must not receive manual lead assignments (no reassign prompt on deactivate).
ROLES_WITHOUT_LEAD_ASSIGNMENTS = frozenset(
    {
        Role.DATA_ENTRY.value,
        Role.RECEPTION.value,
    }
)


def role_offers_lead_reassign_prompt(role: str) -> bool:
    return role not in ROLES_WITHOUT_LEAD_ASSIGNMENTS


def caller_can_manage_employees(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_admin() and user.company_id:
        return True
    if user.is_supervisor() and user.company_id:
        return user.supervisor_has_permission("manage_users")
    return False


def caller_can_deactivate_target(actor: User, target: User) -> bool:
    if target.role == Role.SUPERVISOR.value:
        return bool(actor.is_admin() and actor.company_id == target.company_id)
    return caller_can_manage_employees(actor)


def _validate_deactivation_target(actor: User, target: User) -> str | None:
    """Return error message or None if valid."""
    if not caller_can_deactivate_target(actor, target):
        if target.role == Role.SUPERVISOR.value:
            return "Only the company owner can deactivate supervisors."
        return "Only admins or supervisors with user management permission can deactivate employees."
    if target.id == actor.id:
        return "You cannot deactivate your own account."
    if not actor.company_id or target.company_id != actor.company_id:
        return "You can only deactivate users in your company."
    if target.role == Role.SUPER_ADMIN.value or target.is_superuser:
        return "This account cannot be deactivated."
    if target.role == Role.ADMIN.value:
        return "Company owners and admins cannot be deactivated here."
    if target.role == Role.SUPERVISOR.value:
        pass
    elif target.role not in DEACTIVATABLE_EMPLOYEE_ROLES:
        return "This user role cannot be deactivated."
    company = target.company
    if company and getattr(company, "owner_id", None) == target.id:
        return "Cannot deactivate the company owner."
    return None


def count_active_employees_for_quota(company, *, exclude_owner_id=None) -> int:
    owner_id = exclude_owner_id if exclude_owner_id is not None else getattr(company, "owner_id", None)
    qs = User.objects.filter(company=company, is_active=True)
    if owner_id:
        qs = qs.exclude(id=owner_id)
    return qs.count()


def get_deactivate_preview(target: User) -> dict:
    assigned_leads_count = Client.objects.filter(
        company_id=target.company_id,
        assigned_to_id=target.id,
    ).count()
    show_lead_reassign_options = role_offers_lead_reassign_prompt(target.role)
    can_reassign = False
    if (
        show_lead_reassign_options
        and target.company_id
        and assigned_leads_count > 0
    ):
        can_reassign = get_least_busy_employee(target.company) is not None
    return {
        "assigned_leads_count": assigned_leads_count,
        "can_reassign": can_reassign,
        "show_lead_reassign_options": show_lead_reassign_options,
    }


def _clear_push_tokens(user: User) -> None:
    user.fcm_token = None
    user.fcm_tokens = []


def _sync_supervisor_permission_inactive(target: User) -> None:
    if target.role != Role.SUPERVISOR.value:
        return
    try:
        sp = target.supervisor_permissions
    except SupervisorPermission.DoesNotExist:
        return
    if sp.is_active:
        sp.is_active = False
        sp.save(update_fields=["is_active"])


def _sync_supervisor_permission_active(target: User) -> None:
    if target.role != Role.SUPERVISOR.value:
        return
    try:
        sp = target.supervisor_permissions
    except SupervisorPermission.DoesNotExist:
        SupervisorPermission.objects.create(user=target, is_active=True)
        return
    if not sp.is_active:
        sp.is_active = True
        sp.save(update_fields=["is_active"])


@transaction.atomic
def deactivate_employee(*, actor: User, target: User, reassign_leads: bool) -> dict:
    err = _validate_deactivation_target(actor, target)
    if err:
        raise ValueError(err)

    company = target.company
    if not company:
        raise ValueError("User has no company.")

    assigned_leads = list(
        Client.objects.filter(company=company, assigned_to_id=target.id).select_related(
            "assigned_to"
        )
    )
    assigned_lead_count = len(assigned_leads)

    if not target.is_active:
        remaining = Client.objects.filter(company=company, assigned_to_id=target.id).count()
        return {
            "user": target,
            "assigned_lead_count": 0,
            "skipped_lead_count": 0,
            "leads_remaining_on_user": remaining,
            "already_inactive": True,
        }

    offers_prompt = role_offers_lead_reassign_prompt(target.role)
    # Data entry / reception: never ask; auto-redistribute stray assignments if any.
    should_redistribute = (
        assigned_leads
        and (
            (offers_prompt and reassign_leads)
            or (not offers_prompt)
        )
    )

    target.is_active = False
    _clear_push_tokens(target)
    target.save(update_fields=["is_active", "fcm_token", "fcm_tokens"])
    _sync_supervisor_permission_inactive(target)

    redistributed = {"assigned_count": 0, "skipped_count": 0}
    if should_redistribute:
        redistributed = distribute_clients_to_least_busy(
            company, assigned_leads, triggered_by=actor
        )

    remaining = Client.objects.filter(company=company, assigned_to_id=target.id).count()

    return {
        "user": target,
        "assigned_lead_count": redistributed["assigned_count"],
        "skipped_lead_count": redistributed["skipped_count"],
        "leads_remaining_on_user": remaining,
        "already_inactive": False,
    }


@transaction.atomic
def reactivate_employee(*, actor: User, target: User) -> User:
    err = _validate_deactivation_target(actor, target)
    if err and "deactivate" in err.lower():
        err = err.replace("deactivate", "reactivate")
    if err:
        raise ValueError(err)

    if target.is_active:
        return target

    target.is_active = True
    target.save(update_fields=["is_active"])
    _sync_supervisor_permission_active(target)
    return target
