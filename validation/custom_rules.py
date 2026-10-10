"""Custom rule strategies registered by name (`custom:<name>`)."""

from __future__ import annotations

import re
from typing import Any, Callable

from validation.messages import default_message

CustomFn = Callable[[Any, dict, dict, dict], dict | None]

_CUSTOM: dict[str, CustomFn] = {}

_WHATSAPP_PATTERNS = [
    r"\[\s*Customer Name\s*\]|\[\s*اسم_العميل\s*\]|\[\s*اسم العميل\s*\]|\{\s*Customer Name\s*\}|\{\s*اسم العميل\s*\}|\{\s*اسم_العميل\s*\}",
    r"\[\s*Phone\s*\]|\[\s*رقم_الهاتف\s*\]|\[\s*رقم الهاتف\s*\]|\[\s*الهاتف\s*\]|\{\s*Phone\s*\}|\{\s*رقم الهاتف\s*\}|\{\s*رقم_الهاتف\s*\}",
    r"\[\s*Employee Name\s*\]|\[\s*اسم_الموظف\s*\]|\[\s*اسم الموظف\s*\]|\{\s*Employee Name\s*\}|\{\s*اسم الموظف\s*\}|\{\s*اسم_الموظف\s*\}",
    r"\[\s*Company\s*\]|\[\s*الشركة\s*\]|\[\s*شركة\s*\]|\[\s*اسم الشركة\s*\]|\{\s*Company\s*\}|\{\s*اسم الشركة\s*\}|\{\s*الشركة\s*\}",
    r"\[\s*Current Date\s*\]|\[\s*التاريخ الحالي\s*\]|\{\s*Current Date\s*\}|\{\s*التاريخ الحالي\s*\}",
    r"\[\s*Current Time\s*\]|\[\s*الوقت الحالي\s*\]|\{\s*Current Time\s*\}|\{\s*الوقت الحالي\s*\}",
    r"\[\s*Status\s*\]|\[\s*الحالة\s*\]|\{\s*Status\s*\}|\{\s*الحالة\s*\}",
    r"\[\s*Stage\s*\]|\[\s*المرحلة\s*\]|\{\s*Stage\s*\}|\{\s*المرحلة\s*\}",
    r"\[\s*Channel\s*\]|\[\s*قناة التواصل\s*\]|\{\s*Channel\s*\}|\{\s*قناة التواصل\s*\}",
    r"\[\s*Visit Type\s*\]|\[\s*نوع الزيارة\s*\]|\{\s*Visit Type\s*\}|\{\s*نوع الزيارة\s*\}",
    r"\[\s*Profession\s*\]|\[\s*المهنة\s*\]|\{\s*Profession\s*\}|\{\s*المهنة\s*\}",
    r"\[\s*Amount\s*\]|\[\s*المبلغ\s*\]|\{\s*Amount\s*\}|\{\s*المبلغ\s*\}",
    r"\[\s*Invoice Number\s*\]|\[\s*رقم_الفاتورة\s*\]|\[\s*رقم الفاتورة\s*\]|\{\s*Invoice Number\s*\}|\{\s*رقم الفاتورة\s*\}",
]


def register_custom(name: str, fn: CustomFn) -> None:
    _CUSTOM[name] = fn


def get_custom(name: str) -> CustomFn | None:
    return _CUSTOM.get(name)


def whatsapp_template_patterns() -> list[str]:
    return list(_WHATSAPP_PATTERNS)


def _issue(code: str, params: dict | None = None) -> dict:
    params = params or {}
    full = code if code.startswith("validation.") else f"validation.{code}"
    return {"code": full, "params": params, "message": default_message(full, params)}


def _whatsapp_template_body(value, params, data, context):
    text = "" if value is None else str(value).strip()
    if not text:
        return _issue("validation.whatsapp_template_body", {"reason": "empty"})
    patterns = params.get("patterns") or _WHATSAPP_PATTERNS
    min_words = int(params.get("min_static_words_per_var") or 3)
    compiled = [re.compile(source, re.IGNORECASE) for source in patterns]
    present = [pattern for pattern in compiled if pattern.search(text)]
    if not present:
        return None
    for pattern in present:
        if re.match(rf"^\s*(?:{pattern.pattern})", text, re.IGNORECASE):
            return _issue("validation.whatsapp_template_body", {"reason": "var_at_start"})
    end_trimmed = re.sub(r"[\s.!?,;:]+$", "", text)
    for pattern in present:
        if re.search(rf"(?:{pattern.pattern})\s*$", end_trimmed, re.IGNORECASE):
            return _issue("validation.whatsapp_template_body", {"reason": "var_at_end"})
    static_text = text
    for pattern in compiled:
        static_text = pattern.sub(" ", static_text)
    word_count = len([word for word in static_text.split() if word])
    if word_count < len(present) * min_words:
        return _issue(
            "validation.whatsapp_template_body",
            {"reason": "too_many_variables", "min_words": min_words},
        )
    return None


def load_builtin_custom_rules() -> None:
    register_custom("whatsapp_template_body", _whatsapp_template_body)
