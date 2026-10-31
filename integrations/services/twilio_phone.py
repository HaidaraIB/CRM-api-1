"""Shared phone normalization for Twilio SMS outbound."""


from validation.phones import to_e164


def normalize_phone_to_e164(phone: str) -> str:
    return to_e164(phone)
