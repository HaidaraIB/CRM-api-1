from django.db import models
from django.db.models import Q


DEFAULT_WEEKLY_HOURS = {
    "sunday": {"enabled": True, "start": "10:00", "end": "17:00"},
    "monday": {"enabled": True, "start": "10:00", "end": "17:00"},
    "tuesday": {"enabled": True, "start": "10:00", "end": "17:00"},
    "wednesday": {"enabled": True, "start": "10:00", "end": "17:00"},
    "thursday": {"enabled": True, "start": "10:00", "end": "17:00"},
    "friday": {"enabled": False, "start": "10:00", "end": "17:00"},
    "saturday": {"enabled": False, "start": "10:00", "end": "17:00"},
}

WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


class DemoBookingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    NOT_CONFIRMED = "not_confirmed", "Not confirmed"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No show"


class DemoBookingSettings(models.Model):
    is_enabled = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, default="Asia/Baghdad")
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    horizon_days = models.PositiveSmallIntegerField(default=14)
    min_notice_hours = models.PositiveSmallIntegerField(default=2)
    weekly_hours = models.JSONField(default=dict)
    intro_en = models.TextField(blank=True, default="")
    intro_ar = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "demo_booking_settings"
        verbose_name = "Demo booking settings"
        verbose_name_plural = "Demo booking settings"

    def __str__(self):
        return f"Demo booking settings (enabled={self.is_enabled})"

    @classmethod
    def get_settings(cls):
        settings_obj, created = cls.objects.get_or_create(pk=1)
        if created or not settings_obj.weekly_hours:
            settings_obj.weekly_hours = dict(DEFAULT_WEEKLY_HOURS)
            settings_obj.save(update_fields=["weekly_hours"])
        return settings_obj


class DemoBookingBlockedDate(models.Model):
    date = models.DateField(unique=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "demo_booking_blocked_dates"
        ordering = ["date"]

    def __str__(self):
        return str(self.date)


class DemoBooking(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    company_name = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=DemoBookingStatus.choices,
        default=DemoBookingStatus.PENDING,
        db_index=True,
    )
    language = models.CharField(max_length=8, default="en")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "demo_bookings"
        ordering = ["-starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["starts_at"],
                condition=Q(status=DemoBookingStatus.PENDING)
                | Q(status=DemoBookingStatus.CONFIRMED),
                name="unique_active_demo_booking_starts_at",
            ),
        ]

    def __str__(self):
        return f"{self.name} @ {self.starts_at}"
