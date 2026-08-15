"""
Mutual exclusion between interchangeable payment gateways.

Stripe, PayTabs and Al Qaseh all take the same card payment, and the product
allows exactly one of them to be live at a time. That rule used to exist only in
the admin panel, which - before toggling a gateway on - looked for a row whose
name contained "stripe" or "paytabs" and PATCHed it off in a separate request.
Three problems with that: a third card gateway was invisible to the hardcoded
pair, two requests are not atomic (a crash between them, or two operators
toggling at once, leaves both gateways live), and any other API client bypassed
the rule entirely.

Membership now lives on the adapter (`exclusive_group`), and every server write
path that can enable a gateway comes through `apply_exclusive_activation`.
"""
from __future__ import annotations

import logging

from django.db import transaction

from subscriptions.gateways.registry import conflicting_gateway_rows
from subscriptions.models import PaymentGateway, PaymentGatewayStatus

logger = logging.getLogger(__name__)


def apply_exclusive_activation(gateway: PaymentGateway) -> list[str]:
    """
    Disable the gateways that cannot coexist with a just-enabled `gateway`.

    Returns the names of the rows that were disabled, so callers can tell the
    operator what else changed. A no-op (empty list) when `gateway` is not
    enabled, has no adapter, or belongs to no exclusive group.
    """
    if not (gateway.enabled and gateway.status == PaymentGatewayStatus.ACTIVE.value):
        return []

    with transaction.atomic():
        rivals = conflicting_gateway_rows(gateway)
        if not rivals:
            return []

        # Re-read under a row lock: between resolving the rivals and writing
        # them off, a concurrent request could be enabling one of them.
        locked = list(
            PaymentGateway.objects.select_for_update().filter(
                pk__in=[row.pk for row in rivals]
            )
        )
        disabled = []
        for row in locked:
            row.enabled = False
            row.status = PaymentGatewayStatus.DISABLED.value
            row.save(update_fields=["enabled", "status", "updated_at"])
            disabled.append(row.name)

    # Names only - PaymentGateway.config holds live gateway secrets.
    logger.info(
        "Enabling gateway %r disabled %s", gateway.name, disabled or "no other gateway"
    )
    return disabled
