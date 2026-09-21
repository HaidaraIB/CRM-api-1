"""Approve / not-confirm demo bookings and notify guests."""

from __future__ import annotations

import logging

from django.db import transaction

from accounts.platform_whatsapp import normalize_phone_digits, send_admin_message

from .models import DemoBooking, DemoBookingSettings, DemoBookingStatus

logger = logging.getLogger(__name__)


class DemoBookingTransitionError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _guest_whatsapp_body(booking: DemoBooking, settings_obj: DemoBookingSettings, *, approved: bool) -> str:
    from accounts.event_emails import _format_demo_booking_local_time

    lang = (booking.language or "en").lower()
    when_str = _format_demo_booking_local_time(booking, settings_obj)
    tz_label = settings_obj.timezone or "Asia/Baghdad"
    ref = f"#{booking.id}"
    if approved:
        if lang == "ar":
            return (
                f"LOOP CRM: تم تأكيد جلسة شرح النظام {ref} "
                f"في {when_str} ({tz_label}). نراك في الموعد."
            )
        return (
            f"LOOP CRM: Your walkthrough {ref} is confirmed for "
            f"{when_str} ({tz_label}). See you then."
        )
    if lang == "ar":
        return (
            f"LOOP CRM: لم نتمكن من تأكيد جلسة الشرح {ref} "
            f"في {when_str} ({tz_label}). يمكنك اختيار موعد آخر من صفحة الحجز أو التواصل معنا."
        )
    return (
        f"LOOP CRM: We could not confirm your walkthrough {ref} "
        f"at {when_str} ({tz_label}). Please pick another time on the booking page or contact us."
    )


def _send_guest_whatsapp(booking: DemoBooking, settings_obj: DemoBookingSettings, *, approved: bool) -> None:
    digits = normalize_phone_digits(booking.phone)
    if not digits:
        logger.warning("Demo booking id=%s has no phone digits for WhatsApp", booking.id)
        return
    body = _guest_whatsapp_body(booking, settings_obj, approved=approved)
    ok, detail = send_admin_message(digits, body)
    if not ok:
        logger.warning(
            "Demo booking WhatsApp failed id=%s approved=%s detail=%s",
            booking.id,
            approved,
            detail,
        )


def approve_demo_booking(booking: DemoBooking) -> DemoBooking:
    with transaction.atomic():
        locked = DemoBooking.objects.select_for_update().get(pk=booking.pk)
        if locked.status != DemoBookingStatus.PENDING:
            raise DemoBookingTransitionError("not_pending")
        locked.status = DemoBookingStatus.CONFIRMED
        locked.save(update_fields=["status", "updated_at"])
    settings_obj = DemoBookingSettings.get_settings()
    from accounts.event_emails import send_demo_booking_confirmation_email

    send_demo_booking_confirmation_email(locked, settings_obj)
    _send_guest_whatsapp(locked, settings_obj, approved=True)
    return locked


def not_confirm_demo_booking(booking: DemoBooking) -> DemoBooking:
    with transaction.atomic():
        locked = DemoBooking.objects.select_for_update().get(pk=booking.pk)
        if locked.status != DemoBookingStatus.PENDING:
            raise DemoBookingTransitionError("not_pending")
        locked.status = DemoBookingStatus.NOT_CONFIRMED
        locked.save(update_fields=["status", "updated_at"])
    settings_obj = DemoBookingSettings.get_settings()
    from accounts.event_emails import send_demo_booking_not_confirmed_email

    send_demo_booking_not_confirmed_email(locked, settings_obj)
    _send_guest_whatsapp(locked, settings_obj, approved=False)
    return locked
