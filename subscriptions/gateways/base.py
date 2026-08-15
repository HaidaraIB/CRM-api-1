"""
The payment gateway contract.

Before this package each gateway was a set of module-level functions found by
naming convention, and "which gateway is this?" was answered by substring
matching `PaymentGateway.name` in four separate places, each with its own alias
list and its own idea of what counts as paid ("A" / "SUCCESS" / "PAID" /
"success" / "paid"). An adapter owns that vocabulary once and normalizes it to
`GatewayResult.state`, so callers branch on payment state rather than on gateway
identity.

Adding a gateway means writing one adapter and registering it - no new branch in
payment_completion, check_payment, or the gateway viewset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional, Protocol, runtime_checkable

# What the gateway says about a payment, in one vocabulary.
#   paid    - money captured; safe to finalize
#   failed  - terminal failure; safe to mark FAILED
#   pending - not resolved yet; check again later
#   unknown - could not determine (network error, unrecognised payload)
PaymentState = Literal["paid", "failed", "pending", "unknown"]

# The card processors (Stripe, PayTabs, Al Qaseh) are interchangeable ways to
# take the same Visa/Mastercard payment, so the product allows exactly one to be
# live at a time. Membership is adapter metadata rather than a hardcoded list of
# slugs, so a fourth card gateway joins the rule by declaring it.
CARD_GROUP = "card"


class GatewayError(Exception):
    """A gateway call failed. Carries what the views need to build a response."""

    def __init__(
        self,
        message: str,
        *,
        details: Optional[dict] = None,
        status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.status_code = status_code


@dataclass(frozen=True)
class CheckoutContext:
    """Everything an adapter needs to open a hosted checkout session."""

    subscription: Any
    target_plan: Any
    billing_cycle: str
    amount_usd: Decimal
    customer_email: str
    customer_name: str
    customer_phone: str = ""
    return_url: str = ""
    callback_url: str = ""

    @property
    def subscription_id(self) -> int:
        return self.subscription.id


@dataclass(frozen=True)
class CheckoutSession:
    """What an adapter hands back after opening a session."""

    tran_ref: str
    checkout_url: str = ""
    expires_at: Optional[datetime] = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayResult:
    """A gateway's answer to "is this paid?", normalized."""

    state: PaymentState
    raw: dict = field(default_factory=dict)

    @property
    def is_paid(self) -> bool:
        return self.state == "paid"

    @property
    def is_failed(self) -> bool:
        return self.state == "failed"


@runtime_checkable
class GatewayAdapter(Protocol):
    """
    One payment gateway.

    `slug` is the stable internal identifier used by URLs and the registry.
    `name_aliases` are the substrings matched against the operator-editable
    `PaymentGateway.name` - the only place that fuzzy matching now lives.
    `exclusive_group` names a set of interchangeable gateways of which at most
    one may be enabled at a time.
    """

    slug: str
    name_aliases: tuple[str, ...]
    exclusive_group: str

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        """Open a hosted checkout session. Raises GatewayError on failure."""
        ...

    def verify(self, tran_ref: str) -> GatewayResult:
        """Re-query the gateway. Must not mutate anything."""
        ...

    def extract_tran_ref(self, request) -> Optional[str]:
        """Pull this gateway's reference out of a return/callback request."""
        ...

    def test_credentials(self, config: dict) -> dict:
        """Validate operator-supplied credentials: {"success": bool, "message": str}."""
        ...

    def session_payload(self, payment, session: "CheckoutSession") -> dict:
        """The create-session response body for this gateway."""
        ...


class BaseGatewayAdapter:
    """
    Shared defaults for adapters.

    Subclasses set `slug`/`name_aliases` and override what they support;
    anything left alone degrades honestly rather than pretending to work.
    """

    slug: str = ""
    name_aliases: tuple[str, ...] = ()
    #: request keys that may carry this gateway's reference, in priority order
    tran_ref_params: tuple[str, ...] = ()
    #: gateways sharing a non-empty group are mutually exclusive - enabling one
    #: disables the others. Empty means the gateway stands alone.
    exclusive_group: str = ""

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        raise NotImplementedError(f"{self.slug} cannot open checkout sessions")

    def verify(self, tran_ref: str) -> GatewayResult:
        raise NotImplementedError(f"{self.slug} cannot verify payments")

    def extract_tran_ref(self, request) -> Optional[str]:
        from subscriptions.views.params import param

        value = param(request, *self.tran_ref_params) if self.tran_ref_params else None
        return str(value) if value else None

    def test_credentials(self, config: dict) -> dict:
        return {
            "success": True,
            "message": "Credentials saved (no API test available for this gateway)",
        }

    def session_payload(self, payment, session: CheckoutSession) -> dict:
        """
        The create-session response body.

        Each gateway shipped its own key names before this refactor and the
        frontends read them, so the shapes stay per-gateway rather than being
        unified into something the clients would have to relearn.
        """
        return {
            "payment_id": payment.id,
            "redirect_url": session.checkout_url,
            "tran_ref": session.tran_ref,
        }

    def session_from_payment(self, payment) -> CheckoutSession:
        """Rebuild a CheckoutSession from a stored reusable payment row."""
        return CheckoutSession(
            tran_ref=payment.tran_ref or "",
            checkout_url=payment.checkout_url or "",
            expires_at=payment.session_expires_at,
            meta=payment.session_meta or {},
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} slug={self.slug!r}>"
