"""Query-param filters for Client list and status-counts endpoints."""

from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from django.db.models import Case, DecimalField, F, Q, Value, When
from django.db.models.functions import Greatest, Least
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def _truthy_param(value):
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _parse_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _end_of_day(d):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(d, time(23, 59, 59, 999000)), tz)


def _csv_values(params, key):
    """Split comma-separated query param into non-empty values (ignores 'all')."""
    raw = (params.get(key) or "").strip()
    if not raw:
        return []
    return [
        part.strip()
        for part in raw.split(",")
        if part.strip() and part.strip().lower() != "all"
    ]


def _filter_multi_iexact(queryset, field, values):
    if not values:
        return queryset
    normalized = [v.lower() for v in values]
    if len(normalized) == 1:
        return queryset.filter(**{f"{field}__iexact": normalized[0]})
    return queryset.filter(**{f"{field}__in": normalized})


def apply_client_list_filters(queryset, request, *, exclude_status=False):
    """Apply list query params to a permission-scoped Client queryset."""
    params = request.query_params

    type_values = _csv_values(params, "type")
    if type_values:
        queryset = _filter_multi_iexact(queryset, "type", type_values)

    priority_values = _csv_values(params, "priority")
    if priority_values:
        queryset = _filter_multi_iexact(queryset, "priority", priority_values)

    if not exclude_status:
        status_values = _csv_values(params, "status")
        if status_values:
            if len(status_values) == 1:
                queryset = queryset.filter(status__name=status_values[0])
            else:
                queryset = queryset.filter(status__name__in=status_values)

    if _truthy_param(params.get("assigned_to_me")):
        queryset = queryset.filter(assigned_to=request.user)
    else:
        assigned_values = _csv_values(params, "assigned_to")
        if assigned_values:
            user_ids = []
            include_unassigned = False
            for value in assigned_values:
                if value.lower() == "unassigned":
                    include_unassigned = True
                else:
                    try:
                        user_ids.append(int(value))
                    except (TypeError, ValueError):
                        pass
            assignee_q = Q()
            if user_ids:
                assignee_q |= Q(assigned_to_id__in=user_ids)
            if include_unassigned:
                assignee_q |= Q(assigned_to__isnull=True)
            if assignee_q:
                queryset = queryset.filter(assignee_q)

    comm_values = _csv_values(params, "communication_way")
    if comm_values:
        comm_ids = [int(v) for v in comm_values if v.isdigit()]
        comm_names = [v for v in comm_values if not v.isdigit()]
        comm_q = Q()
        if comm_ids:
            comm_q |= Q(communication_way_id__in=comm_ids)
        if comm_names:
            comm_q |= Q(communication_way__name__in=comm_names)
        if comm_q:
            queryset = queryset.filter(comm_q)

    budget_min = _parse_decimal(params.get("budget_min"))
    budget_max = _parse_decimal(params.get("budget_max"))
    if budget_min is not None or budget_max is not None:
        lo = budget_min if budget_min is not None else Decimal("-999999999999")
        hi = budget_max if budget_max is not None else Decimal("999999999999")
        if lo > hi:
            lo, hi = hi, lo

        queryset = queryset.filter(Q(budget__isnull=False) | Q(budget_max__isnull=False))
        queryset = queryset.annotate(
            budget_low=Case(
                When(
                    budget__isnull=False,
                    budget_max__isnull=False,
                    then=Least(F("budget"), F("budget_max")),
                ),
                When(budget__isnull=False, then=F("budget")),
                When(budget_max__isnull=False, then=F("budget_max")),
                default=Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            budget_high=Case(
                When(
                    budget__isnull=False,
                    budget_max__isnull=False,
                    then=Greatest(F("budget"), F("budget_max")),
                ),
                When(budget__isnull=False, then=F("budget")),
                When(budget_max__isnull=False, then=F("budget_max")),
                default=Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ).filter(budget_high__gte=lo, budget_low__lte=hi)

    created_from = (params.get("created_at_from") or "").strip()
    if created_from:
        dt = parse_datetime(created_from) or (
            timezone.make_aware(datetime.combine(parse_date(created_from), time.min))
            if parse_date(created_from)
            else None
        )
        if dt:
            queryset = queryset.filter(created_at__gte=dt)

    created_to = (params.get("created_at_to") or "").strip()
    if created_to:
        parsed_date = parse_date(created_to)
        dt = parse_datetime(created_to) or (_end_of_day(parsed_date) if parsed_date else None)
        if dt:
            queryset = queryset.filter(created_at__lte=dt)

    return queryset
