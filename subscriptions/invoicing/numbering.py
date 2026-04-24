"""
Sequential invoice numbers (INV-YYYY-NNNNN) backed by InvoiceSequence.
"""
from __future__ import annotations

from django.db import transaction

from subscriptions.models import InvoiceSequence


def next_invoice_number(year: int) -> str:
    """
    Allocate the next invoice number for ``year`` (must run inside ``transaction.atomic()``
    when paired with ``Invoice`` creation, or standalone for tests).
    """
    with transaction.atomic():
        seq, _ = InvoiceSequence.objects.select_for_update().get_or_create(
            year=year,
            defaults={"last_number": 0},
        )
        seq.last_number += 1
        seq.save(update_fields=["last_number", "updated_at"])
        return f"INV-{year}-{seq.last_number:05d}"
