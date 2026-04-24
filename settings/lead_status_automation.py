"""Reserved LeadStatus.automation_key values and helpers for CRM automation."""

VISITED_AUTOMATION_KEY = "visited"


def ensure_visited_lead_status(company):
    """
    Ensure the company has an active LeadStatus row for post-visit automation.
    Idempotent; safe to call on registration and from signals.
    """
    if not company or getattr(company, "specialization", None) not in (
        "real_estate",
        "services",
    ):
        return None

    from .models import LeadStatus, StatusCategory

    status, _ = LeadStatus.objects.get_or_create(
        company=company,
        automation_key=VISITED_AUTOMATION_KEY,
        defaults={
            "name": "Visited",
            "category": StatusCategory.ACTIVE.value,
            "color": "#6366f1",
            "is_default": False,
            "is_hidden": False,
            "is_active": True,
        },
    )
    return status


def get_visited_lead_status(company):
    """Return the active Visited status for this company, or None."""
    if not company:
        return None
    from .models import LeadStatus

    return LeadStatus.objects.filter(
        company=company,
        automation_key=VISITED_AUTOMATION_KEY,
        is_active=True,
    ).first()
