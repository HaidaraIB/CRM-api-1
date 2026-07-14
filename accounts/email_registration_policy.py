from settings.models import SystemSettings


def effective_registration_email_verification_required() -> bool:
    return bool(SystemSettings.get_settings().registration_email_verification_required)
