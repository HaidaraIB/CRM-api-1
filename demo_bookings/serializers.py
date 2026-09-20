from rest_framework import serializers

from .models import (
    WEEKDAY_KEYS,
    DemoBooking,
    DemoBookingBlockedDate,
    DemoBookingSettings,
    DemoBookingStatus,
)


class DemoBookingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemoBookingSettings
        fields = [
            "is_enabled",
            "timezone",
            "duration_minutes",
            "horizon_days",
            "min_notice_hours",
            "weekly_hours",
            "intro_en",
            "intro_ar",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_duration_minutes(self, value):
        if value < 5 or value > 240:
            raise serializers.ValidationError("Duration must be between 5 and 240 minutes.")
        return value

    def validate_horizon_days(self, value):
        if value < 1 or value > 90:
            raise serializers.ValidationError("Horizon must be between 1 and 90 days.")
        return value

    def validate_min_notice_hours(self, value):
        if value > 168:
            raise serializers.ValidationError("Minimum notice cannot exceed 168 hours.")
        return value

    def validate_weekly_hours(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("weekly_hours must be an object.")
        for key in WEEKDAY_KEYS:
            if key not in value:
                continue
            day = value[key]
            if not isinstance(day, dict):
                raise serializers.ValidationError(f"Invalid config for {key}.")
        return value


class DemoBookingPublicConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemoBookingSettings
        fields = ["is_enabled", "timezone", "duration_minutes", "horizon_days", "intro_en", "intro_ar"]


class DemoBookingBlockedDateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemoBookingBlockedDate
        fields = ["id", "date", "reason", "created_at"]
        read_only_fields = ["id", "created_at"]


class DemoBookingCreateSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32)
    company_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    language = serializers.CharField(max_length=8, required=False, default="en")

    def validate_language(self, value):
        lang = (value or "en").lower()
        return lang if lang in ("en", "ar") else "en"


class DemoBookingPublicLookupSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField(min_value=1)
    email = serializers.EmailField()

class DemoBookingListSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemoBooking
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "company_name",
            "notes",
            "starts_at",
            "ends_at",
            "status",
            "language",
            "created_at",
        ]


class DemoBookingStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = DemoBooking
        fields = ["status"]

    def validate_status(self, value):
        if value not in DemoBookingStatus.values:
            raise serializers.ValidationError("Invalid status.")
        return value
