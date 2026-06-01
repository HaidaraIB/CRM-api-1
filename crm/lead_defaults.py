"""Shared helpers for default lead settings (status, etc.)."""


def get_default_lead_status(company):
    from settings.models import LeadStatus

    default = LeadStatus.objects.filter(
        company=company,
        is_active=True,
        is_hidden=False,
        is_default=True,
    ).first()
    if default:
        return default
    return (
        LeadStatus.objects.filter(company=company, is_active=True, is_hidden=False)
        .order_by("id")
        .first()
    )


def get_default_lead_status_id(company) -> int | None:
    status = get_default_lead_status(company)
    return status.id if status else None
