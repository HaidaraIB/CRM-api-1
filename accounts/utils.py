import logging
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from settings.models import SMTPSettings

logger = logging.getLogger(__name__)


def _get_smtp_connection():
    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        raise RuntimeError(
            "SMTP is not active. Please configure SMTP settings before sending emails."
        )

    return EmailBackend(
        host=smtp_settings.host,
        port=smtp_settings.port,
        username=smtp_settings.username,
        password=smtp_settings.password,
        use_tls=smtp_settings.use_tls,
        use_ssl=smtp_settings.use_ssl,
        fail_silently=False,
    )


def _get_frontend_base_url():
    base_url = getattr(settings, "FRONTEND_APP_URL", "http://localhost:3000")
    return base_url.rstrip("/")


def send_email_verification(user, verification, language="en"):
    """
    Send email verification message that includes both OTP code and direct link.
    """
    try:
        smtp_settings = SMTPSettings.get_settings()
        if not smtp_settings.is_active:
            logger.warning("SMTP is disabled; skipping verification email.")
            return False

        connection = _get_smtp_connection()
        from_email = (
            f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
            if smtp_settings.from_name
            else smtp_settings.from_email
        )

        frontend_base = _get_frontend_base_url()
        verification_link = (
            f"{frontend_base}/verify-email?token={verification.token}&email={user.email}"
        )

        greeting_name = user.first_name or user.username or (_("there") if language == "en" else "مرحباً")
        
        if language == "ar":
            subject = "تحقق من بريدك الإلكتروني - LOOP CRM"
            text_body = (
                f"مرحباً {greeting_name}،\n\n"
                f"شكراً لك على التسجيل في LOOP CRM. استخدم رمز التحقق أدناه أو "
                f"اضغط على الرابط للتحقق من عنوان بريدك الإلكتروني.\n\n"
                f"رمز التحقق: {verification.code}\n"
                f"رابط التحقق: {verification_link}\n\n"
                f"ينتهي هذا الرمز في {verification.expires_at.strftime('%Y-%m-%d %H:%M %Z')}.\n\n"
                f"إذا ضغطت على \"التحقق لاحقاً\" داخل التطبيق، يمكنك دائماً العودة إلى هذا البريد واستخدام الرابط أعلاه.\n\n"
                f"إذا لم تقم بإنشاء حساب، يمكنك تجاهل هذا البريد الإلكتروني."
            )
            template_name = "accounts/email_verification_ar.html"
        else:
            subject = _("Verify your LOOP CRM email")
            text_body = _(
                "Hi {name},\n\n"
                "Thanks for signing up to LOOP CRM. Use the verification code below or "
                "click the link to verify your email address.\n\n"
                "Verification code: {code}\n"
                "Verification link: {link}\n\n"
                "This code expires on {expiry}.\n\n"
                "If you pressed \"verify later\" inside the app, you can always return to this email and use the link above.\n\n"
                "If you did not create an account, you can ignore this email."
            ).format(
                name=greeting_name,
                code=verification.code,
                link=verification_link,
                expiry=verification.expires_at.strftime("%Y-%m-%d %H:%M %Z"),
            )
            template_name = "accounts/email_verification.html"

        html_content = render_to_string(
            template_name,
            {
                "subject": subject,
                "greeting_name": greeting_name,
                "code": verification.code,
                "verification_link": verification_link,
                "expires_at": verification.expires_at,
                "support_email": smtp_settings.from_email,
                "brand_color": "#6f3ef0",
            },
        )
        plain_body = strip_tags(html_content) or text_body

        email = EmailMultiAlternatives(
            subject=str(subject),
            body=plain_body,
            from_email=from_email,
            to=[user.email],
            connection=connection,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info("Verification email sent to %s", user.email)
        return True
    except Exception as exc:
        logger.error("Failed to send verification email: %s", exc)
        return False


def send_password_reset_email(user, reset, language="en"):
    """
    Send password reset email that includes both OTP code and direct link.
    """
    try:
        smtp_settings = SMTPSettings.get_settings()
        if not smtp_settings.is_active:
            logger.warning("SMTP is disabled; skipping password reset email.")
            return False

        connection = _get_smtp_connection()
        from_email = (
            f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
            if smtp_settings.from_name
            else smtp_settings.from_email
        )

        frontend_base = _get_frontend_base_url()
        reset_link = (
            f"{frontend_base}/reset-password?token={reset.token}&email={user.email}"
        )

        greeting_name = user.first_name or user.username or (_("there") if language == "en" else "مرحباً")
        
        if language == "ar":
            subject = "إعادة تعيين كلمة المرور - LOOP CRM"
            text_body = (
                f"مرحباً {greeting_name}،\n\n"
                f"طلبت إعادة تعيين كلمة المرور لحسابك في LOOP CRM. استخدم رمز إعادة التعيين أدناه أو "
                f"اضغط على الرابط لإعادة تعيين كلمة المرور.\n\n"
                f"رمز إعادة التعيين: {reset.code}\n"
                f"رابط إعادة التعيين: {reset_link}\n\n"
                f"ينتهي هذا الرمز في {reset.expires_at.strftime('%Y-%m-%d %H:%M %Z')}.\n\n"
                f"إذا لم تطلب إعادة تعيين كلمة المرور، يمكنك تجاهل هذا البريد الإلكتروني بأمان.\n\n"
                f"لأسباب أمنية، سينتهي هذا الرابط بعد الاستخدام."
            )
            template_name = "accounts/password_reset_ar.html"
        else:
            subject = _("Reset your LOOP CRM password")
            text_body = _(
                "Hi {name},\n\n"
                "You requested to reset your password for LOOP CRM. Use the reset code below or "
                "click the link to reset your password.\n\n"
                "Reset code: {code}\n"
                "Reset link: {link}\n\n"
                "This code expires on {expiry}.\n\n"
                "If you did not request a password reset, you can safely ignore this email.\n\n"
                "For security reasons, this link will expire after use."
            ).format(
                name=greeting_name,
                code=reset.code,
                link=reset_link,
                expiry=reset.expires_at.strftime("%Y-%m-%d %H:%M %Z"),
            )
            template_name = "accounts/password_reset.html"

        html_content = render_to_string(
            template_name,
            {
                "subject": subject,
                "greeting_name": greeting_name,
                "code": reset.code,
                "reset_link": reset_link,
                "expires_at": reset.expires_at,
                "support_email": smtp_settings.from_email,
                "brand_color": "#6f3ef0",
            },
        )
        plain_body = strip_tags(html_content) or text_body

        email = EmailMultiAlternatives(
            subject=str(subject),
            body=plain_body,
            from_email=from_email,
            to=[user.email],
            connection=connection,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info("Password reset email sent to %s", user.email)
        return True
    except Exception as exc:
        logger.error("Failed to send password reset email: %s", exc)
        return False


def send_two_factor_auth_email(user, two_fa, language="ar"):
    """
    Send two-factor authentication code email.
    """
    try:
        smtp_settings = SMTPSettings.get_settings()
        if not smtp_settings.is_active:
            logger.warning("SMTP is disabled; skipping 2FA email.")
            return False

        connection = _get_smtp_connection()
        from_email = (
            f"{smtp_settings.from_name} <{smtp_settings.from_email}>"
            if smtp_settings.from_name
            else smtp_settings.from_email
        )

        greeting_name = user.first_name or user.username or (_("there") if language == "en" else "مرحباً")
        
        if language == "en":
            subject = _("Two-Factor Authentication Code - LOOP CRM")
            text_body = (
                f"Hi {greeting_name},\n\n"
                f"A login attempt was requested for your LOOP CRM account.\n\n"
                f"Two-factor authentication code: {two_fa.code}\n\n"
                f"This code is valid for 10 minutes only.\n\n"
                f"If you didn't request a login, please ignore this email and change your password immediately.\n\n"
                f"Sent by LOOP CRM · Need help? Reply to this email: {smtp_settings.from_email}"
            )
            template_name = "accounts/two_factor_auth_en.html"
        else:
            subject = "رمز المصادقة الثنائية - LOOP CRM"
            text_body = (
                f"مرحباً {greeting_name},\n\n"
                f"تم طلب تسجيل الدخول إلى حسابك في LOOP CRM.\n\n"
                f"رمز المصادقة الثنائية: {two_fa.code}\n\n"
                f"هذا الرمز صالح لمدة 10 دقائق فقط.\n\n"
                f"إذا لم تطلب تسجيل الدخول، يرجى تجاهل هذا البريد الإلكتروني وتغيير كلمة المرور الخاصة بك.\n\n"
                f"تم الإرسال بواسطة LOOP CRM · تحتاج مساعدة؟ رد على هذا البريد: {smtp_settings.from_email}"
            )
            template_name = "accounts/two_factor_auth.html"

        html_content = render_to_string(
            template_name,
            {
                "subject": subject,
                "greeting_name": greeting_name,
                "code": two_fa.code,
                "expires_at": two_fa.expires_at,
                "support_email": smtp_settings.from_email,
                "brand_color": "#6f3ef0",
            },
        )
        plain_body = strip_tags(html_content) or text_body

        email = EmailMultiAlternatives(
            subject=str(subject),
            body=plain_body,
            from_email=from_email,
            to=[user.email],
            connection=connection,
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        logger.info("2FA email sent to %s", user.email)
        return True
    except Exception as exc:
        logger.error("Failed to send 2FA email: %s", exc)
        return False