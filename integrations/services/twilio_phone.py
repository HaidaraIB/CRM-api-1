"""Shared phone normalization for Twilio SMS outbound."""


def normalize_phone_to_e164(phone: str) -> str:
    to = (phone or "").strip().replace(" ", "").replace("-", "")
    if to.startswith("07") and len(to) >= 10:
        to = "+964" + to[1:]
    elif not to.startswith("+"):
        to = "+" + to
    return to
