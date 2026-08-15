"""
Currency conversion for gateways that charge in IQD.

Plans are priced in USD; PayTabs, QiCard, ZainCash and FIB charge in IQD. The
`SystemSettings.usd_to_iqd_rate` lookup with its 1300 fallback used to be copied
into seven places, three of which spelled the fallback differently and one of
which divided by a hard-coded literal behind a bare except. One rate, one place.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Union

logger = logging.getLogger(__name__)

#: Used only when SystemSettings is unreachable; keep in sync with the model default.
DEFAULT_USD_TO_IQD_RATE = Decimal("1300")

Number = Union[int, float, str, Decimal]


def usd_to_iqd_rate() -> Decimal:
    """Current USD->IQD rate from SystemSettings, or the default if unavailable."""
    try:
        from settings.models import SystemSettings

        rate = Decimal(str(SystemSettings.get_settings().usd_to_iqd_rate))
        if rate > 0:
            return rate
        logger.warning("usd_to_iqd_rate is %s; using default", rate)
    except Exception:
        logger.exception("Could not read usd_to_iqd_rate; using default")
    return DEFAULT_USD_TO_IQD_RATE


def usd_to_iqd(amount_usd: Number) -> Decimal:
    """USD -> IQD, rounded to whole dinars (IQD has no minor unit in practice)."""
    return (Decimal(str(amount_usd)) * usd_to_iqd_rate()).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )


def iqd_to_usd(amount_iqd: Number) -> Decimal:
    """IQD -> USD, rounded to cents."""
    return (Decimal(str(amount_iqd)) / usd_to_iqd_rate()).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def to_usd(amount: Number, currency: str) -> tuple[Decimal, Decimal]:
    """
    Normalize a gateway-charged amount to USD.

    Returns (amount_usd, exchange_rate) so both can be stored on the Payment row.
    """
    code = (currency or "USD").upper()
    if code == "IQD":
        rate = usd_to_iqd_rate()
        return (
            (Decimal(str(amount)) / rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            rate,
        )
    return Decimal(str(amount)).quantize(Decimal("0.01")), Decimal("1")
