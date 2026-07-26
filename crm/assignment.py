"""
Smart lead auto-assignment: workload by pipeline status, fair tie-breaking, day-off rules.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Count, F, FloatField, Q
from django.db.models.functions import Coalesce

from accounts.models import Role, User
from companies.models import Company
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


def _eligible_assignees_queryset(company):
    """Active employees (and doctors for medical) eligible for lead assignment."""
    return User.objects.filter(
        company=company,
        role__in=_assignment_role_filter(company),
        is_active=True,
    ).select_related("company").order_by("id")


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


def get_round_robin_employee(company):
    """
    Pick the next assignee by simple rotation among eligible users.

    Skips users on their weekly day off. Uses ``Company.last_auto_assigned_employee``
    as the rotation pointer.
    """
    if not company:
        return None

    employees = list(_eligible_assignees_queryset(company))
    available = [employee for employee in employees if user_accepts_new_assignments(employee)]
    if not available:
        return None

    return _pick_round_robin_among_tied(company, available)


def get_auto_assign_employee(company):
    """Pick an assignee using the company's configured auto-assign algorithm."""
    if not company:
        return None

    algorithm = getattr(company, "auto_assign_algorithm", None) or Company.AutoAssignAlgorithm.LEAST_BUSY
    if algorithm == Company.AutoAssignAlgorithm.ROUND_ROBIN:
        return get_round_robin_employee(company)
    return get_least_busy_employee(company)


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


def _workload_weight_for_client(client) -> float:
    """Match annotated workload: active/follow-up/null = 1, inactive = 0.5, closed = 0."""
    status = getattr(client, "status", None)
    if status is None:
        return 1.0
    category = getattr(status, "category", None)
    if category in _ACTIVE_WORKLOAD_CATEGORIES:
        return 1.0
    if category == _INACTIVE_WORKLOAD_CATEGORY:
        return _INACTIVE_WORKLOAD_WEIGHT
    return 0.0


def _next_among_tied(tied_employees, last_id):
    """In-memory fair rotation among tied employees (same rules as DB pointer)."""
    tied_employees = sorted(tied_employees, key=lambda employee: employee.id)
    if len(tied_employees) == 1:
        return tied_employees[0]
    employee_ids = [employee.id for employee in tied_employees]
    if last_id in employee_ids:
        next_index = (employee_ids.index(last_id) + 1) % len(employee_ids)
    else:
        next_index = 0
    return tied_employees[next_index]


def plan_bulk_auto_assignments(company, clients):
    """
    Plan assignees for many leads without per-lead DB workload queries or FCM.

    Loads eligible employees once, picks in memory (updating workload / RR pointer
    locally), then writes ``Company.last_auto_assigned_employee`` once.

    Returns a list of ``User | None`` aligned with *clients* (None = skipped).
    Single-lead paths should keep using ``get_auto_assign_employee``.
    """
    clients = list(clients)
    if not company or not clients:
        return [None] * len(clients)

    algorithm = (
        getattr(company, "auto_assign_algorithm", None)
        or Company.AutoAssignAlgorithm.LEAST_BUSY
    )
    use_least_busy = algorithm != Company.AutoAssignAlgorithm.ROUND_ROBIN

    if use_least_busy:
        pool = list(_employees_with_workload_queryset(company))
        scores = {
            employee.id: float(getattr(employee, "workload_score", 0) or 0)
            for employee in pool
        }
    else:
        pool = list(_eligible_assignees_queryset(company))
        scores = {employee.id: 0.0 for employee in pool}

    available = [
        employee for employee in pool if user_accepts_new_assignments(employee)
    ]
    if not available:
        return [None] * len(clients)

    by_id = {employee.id: employee for employee in available}
    last_id = company.last_auto_assigned_employee_id
    picks = []

    for client in clients:
        if use_least_busy:
            min_score = min(scores[employee.id] for employee in available)
            tied = [
                employee
                for employee in available
                if scores[employee.id] == min_score
            ]
        else:
            tied = available

        selected = _next_among_tied(tied, last_id)
        last_id = selected.id
        scores[selected.id] = scores[selected.id] + _workload_weight_for_client(client)
        picks.append(by_id[selected.id])

    if picks and any(picks):
        with transaction.atomic():
            locked_company = Company.objects.select_for_update().get(pk=company.pk)
            locked_company.last_auto_assigned_employee_id = last_id
            locked_company.save(update_fields=["last_auto_assigned_employee"])
            company.last_auto_assigned_employee_id = last_id

    return picks
