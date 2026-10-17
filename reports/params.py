"""
Parsing and validation of the query string shared by every ``/reports/v2/`` section.

``start``/``end`` are ISO-8601. A datetime without an offset is read in the *company* timezone;
a date-only ``start`` means 00:00 that day and a date-only ``end`` means the end of that day.
The range is half-open ``[start, end)`` everywhere, so adjacent periods never double count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from rest_framework.exceptions import ErrorDetail, ValidationError

from crm.availability import zone_for_company

MAX_SPAN = timedelta(days=366 * 2)
GRANULARITIES = ("hour", "day", "week", "month")
LEAD_TYPES = {"fresh", "hot", "cold"}


def _bad(field_name: str, code: str, message: str):
    raise ValidationError({field_name: [ErrorDetail(message, code=f"validation.{code}")]})


@dataclass
class ReportParams:
    company: object
    tz: object
    start: datetime
    end: datetime
    compare: bool
    granularity: str
    user_ids: list = field(default_factory=list)
    status_ids: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    campaign_ids: list = field(default_factory=list)
    channel_ids: list = field(default_factory=list)
    lead_types: list = field(default_factory=list)

    @property
    def previous(self) -> tuple[datetime, datetime]:
        span = self.end - self.start
        return self.start - span, self.start


def _parse_moment(raw: str, tz, field_name: str, *, is_end: bool) -> datetime:
    raw = (raw or "").strip()
    if not raw:
        _bad(field_name, "required", f"{field_name} is required.")
    try:
        if len(raw) == 10:
            d = date.fromisoformat(raw)
            base = datetime.combine(d, time.min, tzinfo=tz)
            return base + timedelta(days=1) if is_end else base
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _bad(field_name, "invalid_datetime", f"{field_name} must be an ISO-8601 date or datetime.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _int_list(request, name: str) -> list[int]:
    out = []
    for part in (request.query_params.get(name) or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            _bad(name, "invalid_id_list", f"{name} must be a comma-separated list of ids.")
    return out


def _str_list(request, name: str) -> list[str]:
    return [p.strip() for p in (request.query_params.get(name) or "").split(",") if p.strip()]


def auto_granularity(start: datetime, end: datetime) -> str:
    span = end - start
    if span <= timedelta(days=2):
        return "hour"
    if span <= timedelta(days=62):
        return "day"
    if span <= timedelta(days=400):
        return "week"
    return "month"


def parse_params(request) -> ReportParams:
    company = request.user.company
    tz = zone_for_company(company)
    q = request.query_params
    start = _parse_moment(q.get("start"), tz, "start", is_end=False)
    end = _parse_moment(q.get("end"), tz, "end", is_end=True)
    if end <= start:
        _bad("end", "end_before_start", "end must be after start.")
    if end - start > MAX_SPAN:
        _bad("end", "range_too_long", "The report period cannot exceed 2 years.")

    granularity = (q.get("granularity") or "").strip().lower() or auto_granularity(start, end)
    if granularity not in GRANULARITIES:
        _bad("granularity", "invalid_choice", f"granularity must be one of {', '.join(GRANULARITIES)}.")

    lead_types = _str_list(request, "lead_types")
    if any(t not in LEAD_TYPES for t in lead_types):
        _bad("lead_types", "invalid_choice", "lead_types must be fresh, hot or cold.")

    return ReportParams(
        company=company,
        tz=tz,
        start=start,
        end=end,
        compare=(q.get("compare") or "").strip().lower() in ("previous", "1", "true"),
        granularity=granularity,
        user_ids=_int_list(request, "user_ids"),
        status_ids=_int_list(request, "status_ids"),
        sources=_str_list(request, "sources"),
        campaign_ids=_int_list(request, "campaign_ids"),
        channel_ids=_int_list(request, "channel_ids"),
        lead_types=lead_types,
    )
