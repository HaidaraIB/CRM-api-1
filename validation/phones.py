"""
The one place phone numbers are normalized. Stored format is +E.164.

`to_wire_digits` is for providers that want digits without "+" (WhatsApp Cloud,
OTPIQ) and for comparison keys. Nothing else should re-implement these rules.
"""

from __future__ import annotations

import re

from django.db import models

_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹"
_LATIN_DIGITS = "01234567890123456789"
_DIGIT_MAP = str.maketrans(_ARABIC_DIGITS, _LATIN_DIGITS)

_NON_PHONE_VALUES = ("<unknown>", "unknown", "anonymous", "s", "h", "i")
# Bare extensions (e.g. 104) are not numbers; a real one has at least this many digits.
_MIN_DIALABLE_DIGITS = 7


def digits_only(phone: str | None) -> str:
    return re.sub(r"\D", "", str(phone or "").translate(_DIGIT_MAP))


def is_dialable(phone: str | None) -> bool:
    cleaned = str(phone or "").strip()
    if not cleaned or cleaned.lower() in _NON_PHONE_VALUES:
        return False
    return len(digits_only(cleaned)) >= _MIN_DIALABLE_DIGITS


def to_e164(phone: str | None) -> str:
    """Iraq local 07… → +9647…, 00… → +…, bare digits → +digits. Empty stays empty."""
    value = re.sub(r"[\s()\-.]", "", str(phone or "").translate(_DIGIT_MAP))
    if not value:
        return ""
    if value.startswith("07") and len(value) >= 10:
        return "+964" + value[1:]
    if value.startswith("00"):
        return "+" + value[2:]
    return value if value.startswith("+") else "+" + value


def to_wire_digits(phone: str | None) -> str:
    """E.164 without the "+"."""
    return digits_only(to_e164(phone))


def storable_phone(phone: str | None) -> str | None:
    """Value to persist: +E.164 for real numbers; anything else is left as received."""
    if phone is None:
        return None
    cleaned = str(phone).strip()
    return to_e164(cleaned) if is_dialable(cleaned) else cleaned


class E164PhoneField(models.CharField):
    """CharField that stores +E.164 on every write, however the value arrived."""

    def pre_save(self, model_instance, add):
        value = storable_phone(super().pre_save(model_instance, add))
        setattr(model_instance, self.attname, value)
        return value

    def deconstruct(self):
        # Same column as CharField, so swapping the field needs no schema migration.
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.CharField", args, kwargs
