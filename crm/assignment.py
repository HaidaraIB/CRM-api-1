"""
Smart lead auto-assignment: workload by pipeline status, fair tie-breaking, day-off rules.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Count, F, FloatField, Q
from django.db.models.functions import Coalesce

from accounts.models import Role, User
from crm.availability import user_accepts_new_assignments
from settings.models import StatusCategory

# Statuses that represent real sales work in the assignee's queue.
_ACTIVE_WORKLOAD_CATEGORIES = (
    StatusCategory.ACTIVE.value,
    StatusCategory.FOLLOW_UP.value,
)
_INACTIVE_WORKLOAD_CATEGORY = StatusCategory.INACTIVE.value
# Inactive leads count at half weight (still owned, but less demanding than active/follow-up).
_INACTIVE_WORKLOAD_WEIGHT = 0.5


def _assignment_role_filter(company) -> list[str]:
    roles = [Role.EMPLOYEE.value]
    if getattr(company, "specialization", None) == "medical":
        roles.append(Role.DOCTOR.value)
    return roles


def _employees_with_workload_queryset(company):
    """Annotate eligible users with a workload score (lower = more available)."""
    return (
        User.objects.filter(
            company=company,
            role__in=_assignment_role_filter(company),
            is_active=True,
        )
        .annotate(
            active_workload=Count(
                "assigned_clients",
                filter=(
                    Q(assigned_clients__status__category__in=_ACTIVE_WORKLOAD_CATEGORIES)
                    | Q(assigned_clients__status__isnull=True)
                ),
                distinct=True,
            ),
            inactive_workload=Count(
                "assigned_clients",
                filter=Q(
                    assigned_clients__status__category=_INACTIVE_WORKLOAD_CATEGORY
                ),
                distinct=True,
            ),
        )
        .annotate(
            workload_score=Coalesce(
                F("active_workload")
                + F("inactive_workload") * _INACTIVE_WORKLOAD_WEIGHT,
                0.0,
                output_field=FloatField(),
            )
        )
        .order_by("workload_score", "id")
        .select_related("company")
    )


def _pick_round_robin_among_tied(company, tied_employees):
    """Among employees at the minimum workload, rotate fairly using a company pointer."""
    from companies.models import Company

    tied_employees = sorted(tied_employees, key=lambda employee: employee.id)
    employee_ids = [employee.id for employee in tied_employees]

    with transaction.atomic():
        locked_company = Company.objects.select_for_update().get(pk=company.pk)
        if len(tied_employees) == 1:
            selected = tied_employees[0]
        else:
            last_id = locked_company.last_auto_assigned_employee_id
            if last_id in employee_ids:
                current_index = employee_ids.index(last_id)
                next_index = (current_index + 1) % len(employee_ids)
            else:
                next_index = 0
            selected = tied_employees[next_index]

        locked_company.last_auto_assigned_employee = selected
        locked_company.save(update_fields=["last_auto_assigned_employee"])
        return selected


def has_assignable_employee(company) -> bool:
    """True if at least one eligible user can receive a lead today."""
    if not company:
        return False
    employees = _employees_with_workload_queryset(company)
    return any(user_accepts_new_assignments(employee) for employee in employees)


def get_least_busy_employee(company):
    """
    Pick the best assignee for a new or reassigned lead.

    - Workload ignores closed/won/lost pipeline stages (``closed`` category).
    - Active, follow-up, and unset-status leads count fully; inactive leads count half.
    - Skips users on their weekly day off (company timezone).
    - When several users share the minimum workload, rotates among them fairly
      (``Company.last_auto_assigned_employee``), instead of always favoring the lowest user id.
    """
    if not company:
        return None

    employees = list(_employees_with_workload_queryset(company))
    available = [employee for employee in employees if user_accepts_new_assignments(employee)]
    if not available:
        return None

    min_score = min(employee.workload_score for employee in available)
    tied = [employee for employee in available if employee.workload_score == min_score]
    return _pick_round_robin_among_tied(company, tied)
