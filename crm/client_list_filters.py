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


def apply_client_list_filters(queryset, request, *, exclude_status=False):
    """Apply list query params to a permission-scoped Client queryset."""
    params = request.query_params

    type_val = (params.get("type") or "").strip()
    if type_val and type_val.lower() != "all":
        queryset = queryset.filter(type__iexact=type_val)

    priority_val = (params.get("priority") or "").strip()
    if priority_val and priority_val.lower() != "all":
        queryset = queryset.filter(priority__iexact=priority_val)

    if not exclude_status:
        status_val = (params.get("status") or "").strip()
        if status_val and status_val.lower() != "all":
            queryset = queryset.filter(status__name=status_val)

    if _truthy_param(params.get("assigned_to_me")):
        queryset = queryset.filter(assigned_to=request.user)
    else:
        assigned_to_val = (params.get("assigned_to") or "").strip()
        if assigned_to_val and assigned_to_val.lower() != "all":
            if assigned_to_val.lower() == "unassigned":
                queryset = queryset.filter(assigned_to__isnull=True)
            else:
                try:
                    queryset = queryset.filter(assigned_to_id=int(assigned_to_val))
                except (TypeError, ValueError):
                    pass

    comm_val = (params.get("communication_way") or "").strip()
    if comm_val and comm_val.lower() != "all":
        if comm_val.isdigit():
            queryset = queryset.filter(communication_way_id=int(comm_val))
        else:
            queryset = queryset.filter(communication_way__name=comm_val)

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
