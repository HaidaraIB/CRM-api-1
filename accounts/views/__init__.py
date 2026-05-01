"""Account HTTP endpoints; URLconf uses ``from accounts.views import ...``."""
from .email_verify import check_registration_availability, resend_verification, verify_email
from .fcm_language import (
    fcm_diagnostics_full,
    remove_fcm_token,
    update_fcm_token,
    update_language,
)
from .impersonation import impersonate, impersonate_exchange, impersonate_exchange_status
from .limited_supervisor import LimitedAdminViewSet, SupervisorViewSet
from .password_reset import forgot_password, reset_password
from .registration import (
    register_company,
    phone_otp_requirement_settings,
    registration_email_requirement_settings,
)
from .phone_registration import register_phone_send_otp, register_phone_verify_otp
from .email_registration import register_email_send_otp, register_email_verify_otp
from .tokens_users import CustomTokenObtainPairView, UserViewSet
from .two_factor import request_two_factor_auth, verify_two_factor_auth

__all__ = [
    "CustomTokenObtainPairView",
    "UserViewSet",
    "LimitedAdminViewSet",
    "SupervisorViewSet",
    "register_company",
    "phone_otp_requirement_settings",
    "registration_email_requirement_settings",
    "register_phone_send_otp",
    "register_phone_verify_otp",
    "register_email_send_otp",
    "register_email_verify_otp",
    "impersonate",
    "impersonate_exchange",
    "impersonate_exchange_status",
    "verify_email",
    "resend_verification",
    "check_registration_availability",
    "forgot_password",
    "reset_password",
    "request_two_factor_auth",
    "verify_two_factor_auth",
    "update_fcm_token",
    "remove_fcm_token",
    "update_language",
    "fcm_diagnostics_full",
]
