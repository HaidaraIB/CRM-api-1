"""
Business logic for the accounts app, separated from HTTP/view concerns.
"""
import logging

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


def get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def change_user_password(user, current_password, new_password):
    """
    Validate the current password, run Django validators on the new one,
    then set it. Returns (success: bool, error_message: str | None).
    """
    if not user.check_password(current_password):
        return False, "Current password is incorrect."

    try:
        validate_password(new_password, user)
    except ValidationError as e:
        return False, " ".join(e.messages)

    user.set_password(new_password)
    user.save(update_fields=["password"])
    return True, None
