from collections import Counter, defaultdict

from django.db.models import Avg, Count

from crm.models import ClientCall

from .base import client_attr_q, fill_series, trunc
from .employees import classify_call


def build(p) -> dict:
    qs = ClientCall.objects.filter(client__company=p.company, created_at__gte=p.start, created_at__lt=p.end).filter(
        client_attr_q(p, "client__")
    )
    if p.user_ids:
        qs = qs.filter(created_by_id__in=p.user_ids)

    summary = Counter()
    by_user = defaultdict(lambda: Counter())
    user_names = {}
    by_method = defaultdict(lambda: Counter())
    by_hour = [0] * 24
    rows = qs.values_list(
        "created_by_id", "created_by__first_name", "created_by__last_name", "created_by__username",
        "call_method__name", "source", "created_at",
    )
    for uid, first, last, username, method, source, created_at in rows:
        kind = classify_call(method)
        summary["total"] += 1
        summary[kind] += 1
        summary[f"source_{source}"] += 1
        by_hour[created_at.astimezone(p.tz).hour] += 1
        by_method[method or "—"]["total"] += 1
        by_method[method or "—"][kind] += 1
        if uid is not None:
            user_names[uid] = f"{first or ''} {last or ''}".strip() or username
            by_user[uid]["total"] += 1
            by_user[uid][kind] += 1

    series_rows = {r["b"]: {"calls": r["n"]} for r in qs.annotate(b=trunc(p, "created_at")).values("b").annotate(n=Count("id"))}
    avg_rec = qs.filter(recording_duration_sec__isnull=False).aggregate(a=Avg("recording_duration_sec"))["a"]
    return {
        "summary": {
            "total": summary["total"],
            "answered": summary["answered"],
            "missed": summary["missed"],
            "unknown": summary["unknown"],
            "by_source": {k[7:]: v for k, v in summary.items() if k.startswith("source_")},
            "avg_recorded_duration_seconds": round(avg_rec) if avg_rec else None,
        },
        "by_user": sorted(
            ({"id": uid, "name": user_names[uid], **{k: c[k] for k in ("total", "answered", "missed", "unknown")}}
             for uid, c in by_user.items()),
            key=lambda r: -r["total"],
        ),
        "by_method": sorted(
            ({"name": n, **{k: c[k] for k in ("total", "answered", "missed", "unknown")}} for n, c in by_method.items()),
            key=lambda r: -r["total"],
        ),
        "by_hour": by_hour,
        "series": fill_series(p, series_rows, {"calls": 0}),
    }
