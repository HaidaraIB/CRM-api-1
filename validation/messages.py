"""English fallbacks for validation codes. Clients localize `validation.*` keys."""

from __future__ import annotations


def default_message(code: str, params: dict | None = None) -> str:
    params = params or {}
    reason = params.get("reason")
    templates = {
        "validation.required": "This field is required.",
        "validation.min_length": "Must be at least {min} characters.",
        "validation.max_length": "Must be at most {max} characters.",
        "validation.pattern": "Invalid format.",
        "validation.email": "Enter a valid email address.",
        "validation.phone_e164": "Enter a valid phone number.",
        "validation.username": "Use letters, numbers, dots, underscores, or hyphens (min 3).",
        "validation.slug": "Use letters, numbers, and hyphens.",
        "validation.url": "Enter a valid URL.",
        "validation.number_range": "Enter a number in the allowed range.",
        "validation.integer": "Enter a whole number.",
        "validation.decimal_places": "Use at most {places} decimal places.",
        "validation.one_of": "Select a valid option.",
        "validation.matches_field": "Does not match.",
        "validation.required_if": "This field is required.",
        "validation.date_range": "Date is out of range.",
        "validation.date": "Enter a valid date.",
        "validation.file": "File is not allowed.",
        "validation.password_policy": _password_message(reason, params),
        "validation.array": "Check the number of items.",
        "validation.invalid": "Invalid value.",
        "validation.whatsapp_template_body": _whatsapp_message(reason),
    }
    template = templates.get(code, "Invalid value.")
    try:
        return template.format(**{key: params.get(key, "") for key in _placeholders(template)})
    except Exception:
        return template


def _placeholders(template: str) -> list[str]:
    import re

    return re.findall(r"\{(\w+)\}", template)


def _password_message(reason: str | None, params: dict) -> str:
    if reason == "min_length":
        return "Password must be at least {min} characters.".format(min=params.get("min", 8))
    if reason == "numeric":
        return "Password cannot be only numbers."
    if reason == "common":
        return "Password is too common."
    if reason == "similar":
        return "Password is too similar to your account details."
    return "Password does not meet the requirements."


def _whatsapp_message(reason: str | None) -> str:
    if reason == "empty":
        return "Template content is required."
    if reason == "var_at_start":
        return "A variable cannot be at the start of the template."
    if reason == "var_at_end":
        return "A variable cannot be at the end of the template."
    if reason == "too_many_variables":
        return "Add more words around each variable."
    return "Template content is invalid."
