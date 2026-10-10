from decimal import Decimal, ROUND_HALF_UP

_CENTS = Decimal("0.01")
_HUNDRED = Decimal("100")


def money(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def line_total(unit_price, quantity, discount_percentage) -> Decimal:
    gross = money(unit_price) * money(quantity)
    discount = money(discount_percentage) / _HUNDRED
    if discount < 0:
        discount = Decimal("0")
    if discount > 1:
        discount = Decimal("1")
    return (gross * (Decimal("1") - discount)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def recalculate_deal_value(deal) -> None:
    """When line items exist, deal.value is the net of those lines. Otherwise leave value alone."""
    items = list(deal.line_items.all())
    if not items:
        return
    subtotal = sum((item.line_total or Decimal("0")) for item in items)
    subtotal = money(subtotal)
    percentage = money(deal.discount_percentage)
    if percentage > 0:
        deal.discount_amount = (subtotal * percentage / _HUNDRED).quantize(
            _CENTS, rounding=ROUND_HALF_UP
        )
    discount_amount = money(deal.discount_amount)
    net = subtotal - discount_amount
    if net < 0:
        net = Decimal("0.00")
    deal.value = net
    deal.save(update_fields=["value", "discount_amount", "updated_at"])
