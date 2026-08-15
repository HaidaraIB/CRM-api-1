"""
Payment gateway adapters.

Public surface: import from here, not from the individual adapter modules.
"""
from subscriptions.gateways.base import (
    CARD_GROUP,
    BaseGatewayAdapter,
    CheckoutContext,
    CheckoutSession,
    GatewayAdapter,
    GatewayError,
    GatewayResult,
    PaymentState,
)
from subscriptions.gateways.registry import (
    adapter_for_gateway,
    adapter_for_name,
    adapter_for_payment,
    all_adapters,
    autodiscover,
    conflicting_gateway_rows,
    find_gateway_row,
    get_adapter,
    register,
    resolve_gateway,
    rows_in_exclusive_group,
)

__all__ = [
    "CARD_GROUP",
    "BaseGatewayAdapter",
    "CheckoutContext",
    "CheckoutSession",
    "GatewayAdapter",
    "GatewayError",
    "GatewayResult",
    "PaymentState",
    "adapter_for_gateway",
    "adapter_for_name",
    "adapter_for_payment",
    "all_adapters",
    "autodiscover",
    "conflicting_gateway_rows",
    "find_gateway_row",
    "get_adapter",
    "register",
    "resolve_gateway",
    "rows_in_exclusive_group",
]
