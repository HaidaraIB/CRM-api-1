from crm.models import DealEvent


def record_deal_event(
    deal,
    event_type,
    *,
    old_value="",
    new_value="",
    reason="",
    metadata=None,
    created_by=None,
):
    return DealEvent.objects.create(
        deal=deal,
        event_type=event_type,
        old_value=old_value or "",
        new_value=new_value or "",
        reason=reason or "",
        metadata=metadata or {},
        created_by=created_by,
    )
