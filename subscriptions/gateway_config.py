"""
Masking for PaymentGateway.config.

`config` holds live gateway credentials (secretKey, serverKey, merchantSecret,
clientSecret, password, ...). Those must never leave the server in readable
form, but the admin panel still prefills its settings form from this field and
posts the whole form back — so masking alone would write the mask over the real
secret on the next save.

The pair that makes this safe:
  mask_gateway_config()  - what reads return: identifiers plain, secrets masked
  strip_masked_values()  - what writes accept: drop anything still masked, so an
                           unchanged field leaves the stored secret untouched
"""
from __future__ import annotations

MASK_CHAR = "•"
MASK_WIDTH = 8
VISIBLE_TAIL = 4

# Identifiers the operator needs to read back; not credentials.
_PUBLIC_KEYS = {"publishablekey", "publickey", "clientid", "profileid"}

_SECRET_HINTS = ("secret", "password", "token", "privatekey", "key")


def is_secret_key(key: str) -> bool:
    """True when a config key holds a credential rather than an identifier."""
    normalized = key.lower().replace("_", "").replace("-", "")
    if normalized in _PUBLIC_KEYS:
        return False
    return any(hint in normalized for hint in _SECRET_HINTS)


def is_masked(value) -> bool:
    return isinstance(value, str) and MASK_CHAR in value


def mask_value(value) -> str:
    """`sk_live_abcd1234` -> `••••••••1234`; short values are fully masked."""
    text = str(value)
    if len(text) <= VISIBLE_TAIL:
        return MASK_CHAR * MASK_WIDTH
    return MASK_CHAR * MASK_WIDTH + text[-VISIBLE_TAIL:]


def mask_gateway_config(config) -> dict:
    """Read-safe view of a gateway config: secrets masked, identifiers intact."""
    if not isinstance(config, dict):
        return {}
    return {
        key: mask_value(value) if (is_secret_key(key) and value) else value
        for key, value in config.items()
    }


def strip_masked_values(config) -> dict:
    """
    Drop values the client echoed back unchanged from a masked read.

    Without this, saving the settings form without editing the secret fields
    would overwrite real credentials with rows of bullets.
    """
    if not isinstance(config, dict):
        return {}
    return {key: value for key, value in config.items() if not is_masked(value)}


def merge_config_for_write(stored, incoming) -> dict:
    """Stored config with the client's real (non-masked, non-null) edits applied."""
    merged = dict(stored or {})
    merged.update(strip_masked_values(incoming))
    return {key: value for key, value in merged.items() if value is not None}
