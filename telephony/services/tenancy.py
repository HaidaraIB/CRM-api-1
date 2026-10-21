"""
Service-layer tenant guards (ADR 0002 in CRM-pbx: composite FKs emulated here).

Serializers already scope related-object querysets to the caller's company, so a
foreign id fails validation as "does not exist". This is the backstop for code
paths that build rows directly (services, admin actions, management commands).
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError


def assert_same_company(company_id: int, *objs) -> None:
    for obj in objs:
        if obj is None:
            continue
        if getattr(obj, "company_id", None) != company_id:
            # Deliberately vague: do not confirm that the foreign object exists.
            raise ValidationError({"detail": "Related object not found."})
