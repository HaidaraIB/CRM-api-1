"""
The one place that maps a PaymentGateway row to the code that talks to it.

`PaymentGateway.name` is operator-editable free text, so matching it is fuzzy by
necessity - but it used to be fuzzy in four places with four different alias
lists (payment_completion twice, check_payment, and the gateway viewset), which
meant renaming a row in the admin panel could silently break payments in one
path and not another. Now the alias table lives on each adapter and resolution
happens here.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from subscriptions.gateways.base import GatewayAdapter
from subscriptions.models import PaymentGateway, PaymentGatewayStatus

logger = logging.getLogger(__name__)

_ADAPTERS: dict[str, GatewayAdapter] = {}


def register(adapter: GatewayAdapter) -> GatewayAdapter:
    """Register an adapter. Idempotent, so repeated app loads are harmless."""
    if not adapter.slug:
        raise ValueError(f"{adapter!r} must define a slug")
    _ADAPTERS[adapter.slug] = adapter
    return adapter


def all_adapters() -> Iterable[GatewayAdapter]:
    return tuple(_ADAPTERS.values())


def get_adapter(slug: str) -> Optional[GatewayAdapter]:
    return _ADAPTERS.get(slug)


def adapter_for_name(name: str) -> Optional[GatewayAdapter]:
    """Resolve an operator-entered gateway name to its adapter."""
    normalized = (name or "").lower()
    if not normalized:
        return None
    for adapter in _ADAPTERS.values():
        if any(alias in normalized for alias in adapter.name_aliases):
            return adapter
    return None


def adapter_for_gateway(gateway: Optional[PaymentGateway]) -> Optional[GatewayAdapter]:
    if gateway is None:
        return None
    adapter = adapter_for_name(gateway.name)
    if adapter is None:
        logger.warning(
            "No adapter registered for gateway id=%s name=%r", gateway.id, gateway.name
        )
    return adapter


def adapter_for_payment(payment) -> Optional[GatewayAdapter]:
    return adapter_for_gateway(getattr(payment, "payment_method", None))


def find_gateway_row(slug: str) -> Optional[PaymentGateway]:
    """
    The enabled + ACTIVE gateway row for a slug.

    Replaces the five hand-rolled get_<gw>_gateway() helpers, which disagreed
    about whether to filter on status as well as `enabled`.
    """
    adapter = get_adapter(slug)
    if adapter is None:
        return None
    query = PaymentGateway.objects.filter(
        status=PaymentGatewayStatus.ACTIVE.value, enabled=True
    )
    for row in query:
        if adapter_for_name(row.name) is adapter:
            return row
    return None


def rows_in_exclusive_group(group: str, *, enabled_only: bool = True) -> list[PaymentGateway]:
    """
    Gateway rows whose adapter belongs to `group`.

    Resolution goes through `adapter_for_name`, so operator spellings ("Al Qaseh
    IQ", "Stripe Payments") are matched by the same alias table the payment paths
    use rather than by a second list of literals.
    """
    if not group:
        return []
    query = PaymentGateway.objects.all()
    if enabled_only:
        query = query.filter(status=PaymentGatewayStatus.ACTIVE.value, enabled=True)
    return [
        row
        for row in query
        if getattr(adapter_for_name(row.name), "exclusive_group", "") == group
    ]


def conflicting_gateway_rows(gateway: Optional[PaymentGateway]) -> list[PaymentGateway]:
    """
    Enabled rows that cannot coexist with `gateway`, excluding `gateway` itself.

    Empty when the row has no adapter (an operator-invented gateway is nobody's
    rival) or when its adapter declares no exclusive group.
    """
    adapter = adapter_for_gateway(gateway)
    group = getattr(adapter, "exclusive_group", "") if adapter else ""
    if not group:
        return []
    return [row for row in rows_in_exclusive_group(group) if row.pk != gateway.pk]


def resolve_gateway(slug: str) -> tuple[Optional[PaymentGateway], Optional[GatewayAdapter]]:
    """(row, adapter) for a slug; row is None when not configured or disabled."""
    return find_gateway_row(slug), get_adapter(slug)


def autodiscover() -> None:
    """Import the adapter modules so their register() calls run."""
    from subscriptions.gateways import (  # noqa: F401
        alqaseh,
        fib,
        paytabs,
        qicard,
        stripe,
        zaincash,
    )
