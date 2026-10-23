"""
Plan price resolution — the single source of truth for "what does this plan
cost right now?". Billing, previews and serializers all call into here.

Rules (see docs/PRICING_PROMOTIONS.md):
- Regular price is `Plan.price_monthly` / `price_yearly` (unchanged meaning).
- A cycle is *on sale* only when its sale price is set, 0 < sale < regular, and
  the plan's sale window is live at `at`. Otherwise the charged price is regular.
- `discount_percent` is floored so the UI never overstates a discount.
- Sales apply to initial / renewal checkouts. Upgrade proration intentionally
  stays on regular prices (billing.compute_upgrade_proration_usd).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Optional

from django.utils import timezone

from ..models import BillingCycle, Plan

CENT = Decimal("0.01")
PRICING_SNAPSHOT_VERSION = 1


def _money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(CENT)


@dataclass(frozen=True)
class CyclePrice:
    regular: Decimal
    price: Decimal
    on_sale: bool
    discount_percent: int
    sale_ends_at: Optional[datetime]

    @property
    def discount(self) -> Decimal:
        return (self.regular - self.price).quantize(CENT)

    def to_json(self) -> dict:
        return {
            "regular": str(self.regular),
            "price": str(self.price),
            "on_sale": self.on_sale,
            "discount_percent": self.discount_percent,
        }


def sale_window_status(plan: Plan, at: Optional[datetime] = None) -> str:
    """none | scheduled | live | ended — window only, ignores price validity."""
    if plan.sale_price_monthly is None and plan.sale_price_yearly is None:
        return "none"
    at = at or timezone.now()
    if plan.sale_starts_at and plan.sale_starts_at > at:
        return "scheduled"
    if plan.sale_ends_at and plan.sale_ends_at <= at:
        return "ended"
    return "live"


def sale_is_live(plan: Plan, at: Optional[datetime] = None) -> bool:
    return sale_window_status(plan, at) == "live"


def _sale_price_for_cycle(plan: Plan, billing_cycle: str):
    if billing_cycle == BillingCycle.YEARLY:
        return plan.sale_price_yearly
    return plan.sale_price_monthly


def _regular_price_for_cycle(plan: Plan, billing_cycle: str):
    if billing_cycle == BillingCycle.YEARLY:
        return plan.price_yearly
    return plan.price_monthly


def cycle_price(plan: Plan, billing_cycle: str, *, at: Optional[datetime] = None) -> CyclePrice:
    regular = _money(_regular_price_for_cycle(plan, billing_cycle))
    sale_raw = _sale_price_for_cycle(plan, billing_cycle)
    if sale_raw is not None and sale_is_live(plan, at):
        sale = _money(sale_raw)
        if Decimal("0") < sale < regular:
            pct = ((regular - sale) / regular * 100).to_integral_value(rounding=ROUND_FLOOR)
            return CyclePrice(
                regular=regular,
                price=sale,
                on_sale=True,
                discount_percent=int(pct),
                sale_ends_at=plan.sale_ends_at,
            )
    return CyclePrice(regular=regular, price=regular, on_sale=False, discount_percent=0, sale_ends_at=None)


def plan_pricing(plan: Plan, *, at: Optional[datetime] = None) -> dict[str, CyclePrice]:
    at = at or timezone.now()
    return {
        BillingCycle.MONTHLY: cycle_price(plan, BillingCycle.MONTHLY, at=at),
        BillingCycle.YEARLY: cycle_price(plan, BillingCycle.YEARLY, at=at),
    }


def plan_pricing_json(plan: Plan, *, at: Optional[datetime] = None) -> dict:
    return {cycle: cp.to_json() for cycle, cp in plan_pricing(plan, at=at).items()}


@dataclass(frozen=True)
class CheckoutQuote:
    """Everything a checkout needs to know about the amount it will charge."""

    plan: Plan
    billing_cycle: str
    amount_usd: Decimal
    intent: str  # initial | renewal | upgrade
    regular_usd: Decimal
    sale_ends_at: Optional[datetime] = None

    @property
    def discount_usd(self) -> Decimal:
        return max(Decimal("0.00"), (self.regular_usd - self.amount_usd).quantize(CENT))

    def snapshot(self) -> dict:
        """Server-written record stored on Payment.pricing."""
        return {
            "v": PRICING_SNAPSHOT_VERSION,
            "intent": self.intent,
            "billing_cycle": self.billing_cycle,
            "regular_usd": str(self.regular_usd),
            "charged_usd": str(self.amount_usd.quantize(CENT)),
            "discount_usd": str(self.discount_usd),
            "sale_ends_at": self.sale_ends_at.isoformat() if self.sale_ends_at else None,
        }


def locked_amount_from_snapshot(pricing: Optional[dict]) -> Optional[Decimal]:
    """The amount the server asked the gateway to charge, if it was recorded."""
    if not isinstance(pricing, dict):
        return None
    raw = pricing.get("charged_usd")
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw)).quantize(CENT)
    except Exception:
        return None
