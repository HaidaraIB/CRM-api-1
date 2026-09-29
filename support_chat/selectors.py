from __future__ import annotations

from django.db.models import Count, F, Q

from .models import SupportConversation, SupportMessage


def admin_inbox_queryset(search: str | None = None, status_filter: str | None = None):
    """
    status_filter: open | awaiting | pending | resolved | all | None (same as all)
    """
    qs = SupportConversation.objects.select_related(
        "company",
        "company__owner",
    ).order_by(F("last_message_at").desc(nulls_last=True), "-updated_at")

    if search:
        term = search.strip()
        if term:
            qs = qs.filter(
                Q(company__name__icontains=term)
                | Q(company__domain__icontains=term)
                | Q(company__owner__email__icontains=term)
                | Q(company__owner__first_name__icontains=term)
                | Q(company__owner__last_name__icontains=term)
            )

    sf = (status_filter or "all").strip().lower()
    if sf == "open":
        qs = qs.filter(status=SupportConversation.Status.OPEN).exclude(
            last_message_side=SupportConversation.LastMessageSide.TENANT
        )
    elif sf == "resolved":
        qs = qs.filter(status=SupportConversation.Status.RESOLVED)
    elif sf == "pending":
        qs = qs.filter(status=SupportConversation.Status.PENDING)
    elif sf == "awaiting":
        qs = qs.filter(
            Q(status=SupportConversation.Status.PENDING)
            | Q(
                status=SupportConversation.Status.OPEN,
                last_message_side=SupportConversation.LastMessageSide.TENANT,
            )
        )

    qs = qs.annotate(
        support_unread_count=Count(
            "messages",
            filter=Q(messages__side=SupportMessage.Side.TENANT)
            & (
                Q(support_last_read_message_id__isnull=True)
                | Q(messages__id__gt=F("support_last_read_message_id"))
            ),
            distinct=True,
        )
    )
    return qs


def support_unread_count_for_inbox() -> int:
    """Total tenant-side unread messages across all conversations (admin badge)."""
    qs = SupportConversation.objects.all()
    total = 0
    for conv in qs.only("id", "support_last_read_message_id"):
        cursor = conv.support_last_read_message_id or 0
        total += SupportMessage.objects.filter(
            conversation_id=conv.id,
            side=SupportMessage.Side.TENANT,
            id__gt=cursor,
        ).count()
    return total
