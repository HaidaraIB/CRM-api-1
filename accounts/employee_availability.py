"""
Ad-hoc "unavailable for a while" toggle, the short-absence half of employee time off.

Planned leave (``time_off_start_date``/``time_off_end_date``) is edited like any other
schedule field through the user serializer; this module only covers the quick toggle,
whose end is computed server-side from a duration so a skewed client clock cannot buy
a longer — or shorter — absence than requested.

Unlike ``employee_deactivation``, nothing here touches login, push tokens, or lead
ownership: the user keeps working their book, routing just skips them.
"""
from datetime import timedelta

from django.utils import timezone

from accounts.employee_deactivation import caller_can_deactivate_target
from accounts.models import Role, User

# Presets the clients offer; any duration inside the bound is accepted so web and
# mobile can differ without a backend change.
UNAVAILABLE_DURATION_PRESETS = (30, 60, 120, 240, 480)
MAX_UNAVAILABLE_MINUTES = 24 * 60


def _assert_can_set_availability(actor: User, target: User) -> None:
    """PermissionError when *actor* may not manage *target*, ValueError when the target is wrong."""
    if not caller_can_deactivate_target(actor, target):
        raise PermissionError(
            "Only admins or supervisors with user management permission can change availability."
        )
    if not actor.company_id or target.company_id != actor.company_id:
        raise PermissionError("You can only change availability for users in your company.")
    if target.role == Role.SUPER_ADMIN.value or target.is_superuser:
        raise ValueError("This account has no lead routing availability.")


def set_user_unavailable(*, actor: User, target: User, duration_minutes) -> User:
    """
    Mark *target* unavailable for ``duration_minutes``, or available again when the
    duration is None/0. Returns the updated user.
    """
    _assert_can_set_availability(actor, target)

    if not duration_minutes:
        target.unavailable_until = None
        target.save(update_fields=["unavailable_until"])
        return target

    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError):
        raise ValueError("duration_minutes must be a whole number of minutes.")
    if minutes < 1 or minutes > MAX_UNAVAILABLE_MINUTES:
        raise ValueError(
            f"duration_minutes must be between 1 and {MAX_UNAVAILABLE_MINUTES}, "
            "or omitted to mark the user available again."
        )

    target.unavailable_until = timezone.now() + timedelta(minutes=minutes)
    target.save(update_fields=["unavailable_until"])
    return target
