"""Load every domain schema exactly once."""

_LOADED = False


def load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from validation.custom_rules import load_builtin_custom_rules

    load_builtin_custom_rules()
    from validation.schemas import (  # noqa: F401
        activities,
        auth,
        content,
        deals,
        integrations,
        inventory,
        leads,
        settings as settings_schemas,
        subscriptions,
        support,
        tenants,
    )
