"""
Tenant-facing serializers. Every related-object field is re-scoped to the
caller's company in ``CompanyScopedSerializerMixin``, so another tenant's id is
simply "does not exist" (no existence oracle). Secrets are write-only, except
the one-time ``sip_secret`` returned when an extension is created or rotated.
"""

from __future__ import annotations

import re

from django.conf import settings
from rest_framework import serializers

from accounts.models import User
from integrations.encryption import encrypt_token

from . import contract
from .models import (
    DestinationType,
    PbxBusinessHours,
    PbxCall,
    PbxCallEvent,
    PbxCallLeg,
    PbxExtension,
    PbxFraudAlert,
    PbxGatewayPeer,
    PbxInboundRoute,
    PbxIvrMenu,
    PbxIvrOption,
    PbxNode,
    PbxNumber,
    PbxOutboundRoute,
    PbxProvisioningJob,
    PbxQueue,
    PbxQueueMember,
    PbxRecording,
    PbxSound,
    PbxTenant,
    PbxTrunk,
)
from .services.compiler import CompileError, clean_display_name, validate_cidr, validate_host, validate_pattern


class CompanyScopedSerializerMixin:
    """Limit every PrimaryKeyRelatedField to rows of the request user's company."""

    scoped_fields: dict[str, type] = {}

    def get_fields(self):
        fields = super().get_fields()
        company_id = getattr(getattr(self.context.get("request"), "user", None), "company_id", None)
        for name, model in self.scoped_fields.items():
            if name in fields:
                fields[name].queryset = model.objects.filter(company_id=company_id)
        return fields


class PbxExtensionSerializer(CompanyScopedSerializerMixin, serializers.ModelSerializer):
    scoped_fields = {"user": User, "outbound_number": PbxNumber}
    # Shown once, on create and on rotate (set by the view); never stored in plain text.
    sip_secret = serializers.SerializerMethodField()
    sip_username = serializers.SerializerMethodField()

    class Meta:
        model = PbxExtension
        fields = [
            "id", "number", "display_name", "device_type", "user", "status", "outbound_number",
            "sip_username", "sip_secret", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "sip_username", "sip_secret", "created_at", "updated_at"]

    def get_sip_secret(self, obj):
        return self.context.get("one_time_secret") if self.context.get("one_time_secret_for") == obj.pk else None

    def get_sip_username(self, obj):
        tenant = self.context.get("tenant")
        if tenant is None:
            return None
        return contract.extension_endpoint(tenant.realm_key, obj.number)

    def validate_number(self, value):
        value = (value or "").strip()
        try:
            contract.validate_extension(value)
        except contract.NamingError:
            raise serializers.ValidationError("Extension must be 2-6 digits and not start with 0.")
        from .services.emergency import is_emergency

        if is_emergency(value):  # agents dialling it reach emergency services (services/emergency.py)
            raise serializers.ValidationError("This number is reserved for emergency calls.",
                                              code="pbx_emergency_number")
        return value

    def validate_display_name(self, value):
        return clean_display_name(value)

    def validate_outbound_number(self, value):
        if value is None:
            return value
        if value.verified_at is None:
            raise serializers.ValidationError("Choose a verified number.")
        if value.trunk.status == PbxTrunk.Status.DISABLED:
            raise serializers.ValidationError("That number's line is disabled.")
        return value

    def validate(self, attrs):
        company_id = self.context["request"].user.company_id
        number = attrs.get("number", getattr(self.instance, "number", None))
        clash = PbxExtension.objects.filter(company_id=company_id, number=number)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"number": "This extension number is already in use."})
        user = attrs.get("user")
        if user is not None:
            taken = PbxExtension.objects.filter(company_id=company_id, user=user)
            if self.instance is not None:
                taken = taken.exclude(pk=self.instance.pk)
            if taken.exists():
                raise serializers.ValidationError({"user": "This user already has an extension."})
        return attrs


class PbxTrunkSerializer(serializers.ModelSerializer):
    auth_secret = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=128)
    has_auth_secret = serializers.SerializerMethodField()
    # A GSM gateway's trunk (telephony/gateways/) is pinned to its tunnel address.
    gateway = serializers.SerializerMethodField()

    # What enrollment pins on a gateway's trunk; changed only through the gateway itself.
    GATEWAY_PINNED = ("kind", "host", "port", "transport", "allowed_sources")

    class Meta:
        model = PbxTrunk
        fields = [
            "id", "name", "kind", "status", "host", "port", "transport", "auth_username",
            "auth_secret", "has_auth_secret", "allowed_sources", "gateway", "health", "health_changed_at",
            "created_at", "updated_at",
        ]
        # Status moves only through verification (platform operator / health checks).
        read_only_fields = ["id", "status", "has_auth_secret", "gateway", "health", "health_changed_at",
                            "created_at", "updated_at"]

    def get_has_auth_secret(self, obj):
        return bool(obj.auth_secret_encrypted)

    def get_gateway(self, obj):
        peer = getattr(obj, "gateway_peer", None) if obj.pk else None
        return str(peer.pk) if peer is not None else None

    def validate(self, attrs):
        if self.instance is not None and self.get_gateway(self.instance):
            changed = [f for f in self.GATEWAY_PINNED if f in attrs and attrs[f] != getattr(self.instance, f)]
            if changed:
                raise serializers.ValidationError({changed[0]: "This trunk belongs to a GSM gateway; its address is fixed."})
        return attrs

    def validate_host(self, value):
        try:
            return validate_host(value)
        except CompileError:
            raise serializers.ValidationError("Enter a valid hostname or IP address.")

    def validate_port(self, value):
        if not (1 <= value <= 65535):
            raise serializers.ValidationError("Port must be between 1 and 65535.")
        return value

    def validate_auth_username(self, value):
        value = (value or "").strip()
        if value and not all(c.isalnum() or c in "_.+-" for c in value):
            raise serializers.ValidationError("Use letters, digits and _ . + - only.")
        return value

    def validate_auth_secret(self, value):
        if value and not all(c.isalnum() or c in "_-" for c in value):
            raise serializers.ValidationError("Use letters, digits, _ and - only.")
        if value and len(value) < 16:
            raise serializers.ValidationError("Use at least 16 characters.")
        return value

    def validate_allowed_sources(self, value):
        if not isinstance(value, list) or len(value) > 16:
            raise serializers.ValidationError("Provide up to 16 IP addresses or networks.")
        try:
            return [validate_cidr(v) for v in value]
        except CompileError:
            raise serializers.ValidationError("Each entry must be an IP address or CIDR network.")

    def validate_name(self, value):
        value = (value or "").strip()
        company_id = self.context["request"].user.company_id
        clash = PbxTrunk.objects.filter(company_id=company_id, name=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A trunk with this name already exists.")
        return value

    def _apply_secret(self, validated_data):
        if "auth_secret" in validated_data:
            secret = validated_data.pop("auth_secret")
            validated_data["auth_secret_encrypted"] = encrypt_token(secret) if secret else ""
        return validated_data

    def create(self, validated_data):
        return super().create(self._apply_secret(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._apply_secret(validated_data))


class PbxNumberSerializer(CompanyScopedSerializerMixin, serializers.ModelSerializer):
    scoped_fields = {"trunk": PbxTrunk}
    # Declared explicitly to drop the auto UniqueValidator: uniqueness is checked
    # in validate_e164 after normalisation, with an owner-neutral message.
    e164 = serializers.CharField(max_length=20)
    verified = serializers.SerializerMethodField()

    class Meta:
        model = PbxNumber
        fields = ["id", "e164", "label", "trunk", "verified", "verified_at", "created_at", "updated_at"]
        read_only_fields = ["id", "verified", "verified_at", "created_at", "updated_at"]

    def get_verified(self, obj):
        return obj.verified_at is not None

    def validate_e164(self, value):
        value = (value or "").strip().replace(" ", "")
        if not contract.E164_RE.match(value):
            raise serializers.ValidationError("Enter the number in international format, e.g. +9647701234567.")
        clash = PbxNumber.objects.filter(e164=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            # Same message whether the owner is this tenant or another: a DID
            # belongs to exactly one tenant and we do not reveal which.
            raise serializers.ValidationError("This number is already registered.")
        return value

    def update(self, instance, validated_data):
        # Changing the number or its trunk invalidates the ownership verification.
        if ("e164" in validated_data and validated_data["e164"] != instance.e164) or (
            "trunk" in validated_data and validated_data["trunk"].pk != instance.trunk_id
        ):
            validated_data["verified_at"] = None
        return super().update(instance, validated_data)


# ---- destinations (Phase 4) ---------------------------------------------------

_DEST_MESSAGES = {
    "extension": "Choose the extension.",
    "ivr": "Choose the IVR menu.",
    "queue": "Choose the queue.",
}


def validate_destination(attrs, instance, prefix: str, *, queue: bool = True, hangup: bool = True,
                         default=DestinationType.HANGUP) -> str:
    """
    A typed destination (models.DestinationType): ``<prefix>_type`` plus the one
    target that type needs. The other targets are cleared, so a row never carries
    a stale reference. Targets were already scoped to the caller's company by the
    serializer fields. Returns the destination type.
    """
    def name(kind):
        return f"{prefix}_{kind}" if prefix else kind

    type_key = f"{prefix}_type" if prefix else "destination_type"
    dest = attrs.get(type_key, getattr(instance, type_key, default) if instance is not None else default)
    if dest == DestinationType.QUEUE and not queue:
        raise serializers.ValidationError({type_key: "A queue cannot overflow into another queue."})
    if dest == DestinationType.HANGUP and not hangup:
        raise serializers.ValidationError({type_key: "Choose where calls go."})
    for kind in ("extension", "ivr", "queue"):
        if kind == "queue" and not queue:
            continue
        if kind == dest:
            target = attrs.get(name(kind), getattr(instance, name(kind), None) if instance is not None else None)
            if target is None:
                raise serializers.ValidationError({name(kind): _DEST_MESSAGES[kind]})
        else:
            attrs[name(kind)] = None
    return dest


class PbxInboundRouteSerializer(CompanyScopedSerializerMixin, serializers.ModelSerializer):
    scoped_fields = {
        "number": PbxNumber, "extension": PbxExtension, "ivr": PbxIvrMenu, "queue": PbxQueue,
        "business_hours": PbxBusinessHours, "closed_extension": PbxExtension, "closed_ivr": PbxIvrMenu,
        "closed_queue": PbxQueue,
    }

    class Meta:
        model = PbxInboundRoute
        fields = [
            "id", "number", "destination_type", "extension", "ivr", "queue",
            "business_hours", "closed_type", "closed_extension", "closed_ivr", "closed_queue",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        validate_destination(attrs, self.instance, "", hangup=False, default=DestinationType.EXTENSION)
        hours = attrs.get("business_hours", getattr(self.instance, "business_hours", None))
        if hours is None:
            # Without business hours the closed destination is never used.
            attrs.update(closed_type=DestinationType.HANGUP, closed_extension=None, closed_ivr=None, closed_queue=None)
        else:
            validate_destination(attrs, self.instance, "closed")
        number = attrs.get("number")
        if number is not None:
            clash = PbxInboundRoute.objects.filter(number=number)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"number": "This number already has an inbound route."})
        return attrs


class PbxOutboundRouteSerializer(CompanyScopedSerializerMixin, serializers.ModelSerializer):
    scoped_fields = {"trunk": PbxTrunk, "caller_id": PbxNumber}

    class Meta:
        model = PbxOutboundRoute
        fields = ["id", "name", "priority", "patterns", "trunk", "caller_id", "enabled", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_patterns(self, value):
        if not isinstance(value, list) or not (1 <= len(value) <= 50):
            raise serializers.ValidationError("Provide between 1 and 50 destination patterns.")
        out = []
        for item in value:
            try:
                prefix, digits = validate_pattern(item)
            except CompileError as exc:
                raise serializers.ValidationError(str(exc))
            out.append({"prefix": prefix, "digits": digits})
        return out

    def validate_name(self, value):
        value = (value or "").strip()
        company_id = self.context["request"].user.company_id
        clash = PbxOutboundRoute.objects.filter(company_id=company_id, name=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A route with this name already exists.")
        return value

    def validate_caller_id(self, value):
        if value.verified_at is None:
            raise serializers.ValidationError("Caller ID must be a verified number.")
        return value


_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class PbxBusinessHoursSerializer(serializers.ModelSerializer):
    class Meta:
        model = PbxBusinessHours
        fields = ["id", "name", "timezone", "rules", "closed_dates", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        return _unique_name(self, PbxBusinessHours, value, "Business hours with this name already exist.")

    def validate_timezone(self, value):
        from zoneinfo import available_timezones

        value = (value or "").strip()
        if value not in available_timezones():
            raise serializers.ValidationError("Choose a valid time zone, e.g. Asia/Baghdad.")
        return value

    def validate_rules(self, value):
        if not isinstance(value, list) or not (1 <= len(value) <= 14):
            raise serializers.ValidationError("Add between 1 and 14 opening periods.")
        out = []
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each opening period needs days, start and end.")
            days, start, end = item.get("days"), item.get("start"), item.get("end")
            if (not isinstance(days, list) or not days or len(set(days)) != len(days)
                    or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days)):
                raise serializers.ValidationError("Days are 0 (Monday) to 6 (Sunday), each at most once.")
            if not (isinstance(start, str) and _TIME_RE.match(start) and isinstance(end, str) and _TIME_RE.match(end)):
                raise serializers.ValidationError("Times are HH:MM, 00:00 to 23:59.")
            if end <= start:
                raise serializers.ValidationError("Each period must end after it starts.")
            out.append({"days": sorted(days), "start": start, "end": end})
        return out

    def validate_closed_dates(self, value):
        from datetime import date

        if not isinstance(value, list) or len(value) > 100:
            raise serializers.ValidationError("Provide up to 100 closed dates.")
        out = set()
        for item in value:
            try:
                if not (isinstance(item, str) and _DATE_RE.match(item)):
                    raise ValueError
                date.fromisoformat(item)
            except ValueError:
                raise serializers.ValidationError("Dates are YYYY-MM-DD.")
            out.add(item)
        return sorted(out)


def _unique_name(serializer, model, value, message):
    value = (value or "").strip()
    company_id = serializer.context["request"].user.company_id
    clash = model.objects.filter(company_id=company_id, name=value)
    if serializer.instance is not None:
        clash = clash.exclude(pk=serializer.instance.pk)
    if clash.exists():
        raise serializers.ValidationError(message)
    return value


# Platform prompts every node installs under <astdatadir>/sounds/loop/ (CRM-pbx
# deploy/dev/sounds; the runbook installs the real recordings).
IVR_PROMPTS = ["loop/ivr-default", "loop/ivr-welcome-ar", "loop/ivr-welcome-en"]


class _IvrOptionSerializer(serializers.Serializer):
    digit = serializers.ChoiceField(choices=PbxIvrOption.DIGITS)
    destination_type = serializers.ChoiceField(choices=DestinationType.choices)
    extension = serializers.PrimaryKeyRelatedField(queryset=PbxExtension.objects.none(), required=False, allow_null=True)
    ivr = serializers.PrimaryKeyRelatedField(queryset=PbxIvrMenu.objects.none(), required=False, allow_null=True)
    queue = serializers.PrimaryKeyRelatedField(queryset=PbxQueue.objects.none(), required=False, allow_null=True)

    def get_fields(self):
        fields = super().get_fields()
        company_id = getattr(getattr(self.context.get("request"), "user", None), "company_id", None)
        fields["extension"].queryset = PbxExtension.objects.filter(company_id=company_id)
        fields["ivr"].queryset = PbxIvrMenu.objects.filter(company_id=company_id)
        fields["queue"].queryset = PbxQueue.objects.filter(company_id=company_id)
        return fields

    def validate(self, attrs):
        validate_destination(attrs, None, "", default=DestinationType.EXTENSION)
        return attrs


class PbxSoundSerializer(serializers.ModelSerializer):
    """A tenant's own greeting (the audio itself is ``GET sounds/<id>/audio/``)."""

    class Meta:
        model = PbxSound
        fields = ["id", "name", "duration_ms", "size_bytes", "created_at", "updated_at"]
        read_only_fields = ["id", "duration_ms", "size_bytes", "created_at", "updated_at"]

    def validate_name(self, value):
        return _unique_name(self, PbxSound, value, "A greeting with this name already exists.")


class PbxSoundUploadSerializer(PbxSoundSerializer):
    """
    POST /telephony/sounds/ (multipart): ``name`` and ``file``, a WAV of 16-bit
    PCM, mono, 8 kHz, 0.5-60 s. The CRM-project UI converts any audio the browser
    can decode to that before uploading.
    """

    file = serializers.FileField(write_only=True)

    class Meta(PbxSoundSerializer.Meta):
        fields = PbxSoundSerializer.Meta.fields + ["file"]

    def validate_file(self, value):
        from .services import sounds

        if value.size > sounds.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError(f"A greeting is at most {sounds.MAX_SECONDS} seconds.")
        try:
            return sounds.parse_wav(value.read())
        except sounds.SoundError as exc:
            raise serializers.ValidationError(exc.message)

    def create(self, validated_data):
        from .services import sounds

        audio = validated_data.pop("file")
        return PbxSound.objects.create(
            **validated_data, audio=audio, sha256=sounds.sha256_hex(audio), size_bytes=len(audio),
            duration_ms=sounds.duration_ms(audio),
        )


class PbxIvrMenuSerializer(CompanyScopedSerializerMixin, serializers.ModelSerializer):
    scoped_fields = {
        "timeout_extension": PbxExtension, "timeout_ivr": PbxIvrMenu, "timeout_queue": PbxQueue, "greeting": PbxSound,
    }
    prompt = serializers.ChoiceField(choices=IVR_PROMPTS, required=False)
    options = _IvrOptionSerializer(many=True, required=False)

    class Meta:
        model = PbxIvrMenu
        fields = [
            "id", "name", "prompt", "greeting", "timeout_s", "max_retries",
            "timeout_type", "timeout_extension", "timeout_ivr", "timeout_queue",
            "options", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        return _unique_name(self, PbxIvrMenu, value, "An IVR menu with this name already exists.")

    def validate_timeout_s(self, value):
        if not 1 <= value <= 30:
            raise serializers.ValidationError("Wait between 1 and 30 seconds for a key.")
        return value

    def validate_max_retries(self, value):
        if value > 5:
            raise serializers.ValidationError("Replay the menu at most 5 times.")
        return value

    def validate_options(self, value):
        if len(value) > 12:
            raise serializers.ValidationError("A menu has at most 12 options.")
        digits = [o["digit"] for o in value]
        if len(set(digits)) != len(digits):
            raise serializers.ValidationError("Each key can be used once.")
        return value

    def validate(self, attrs):
        validate_destination(attrs, self.instance, "timeout")
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["options"] = [
            {"id": str(o.id), "digit": o.digit, "destination_type": o.destination_type,
             "extension": str(o.extension_id) if o.extension_id else None,
             "ivr": str(o.ivr_id) if o.ivr_id else None,
             "queue": str(o.queue_id) if o.queue_id else None}
            for o in instance.options.all()
        ]
        return data

    def _save_options(self, menu, options):
        if options is None:
            return
        menu.options.all().delete()
        PbxIvrOption.objects.bulk_create([
            PbxIvrOption(company_id=menu.company_id, menu=menu, **o) for o in options
        ])

    def create(self, validated_data):
        options = validated_data.pop("options", [])
        menu = super().create(validated_data)
        self._save_options(menu, options)
        return menu

    def update(self, instance, validated_data):
        options = validated_data.pop("options", None)
        menu = super().update(instance, validated_data)
        self._save_options(menu, options)
        return menu


class _QueueMemberSerializer(serializers.Serializer):
    extension = serializers.PrimaryKeyRelatedField(queryset=PbxExtension.objects.none())
    penalty = serializers.IntegerField(min_value=0, max_value=10, required=False, default=0)

    def get_fields(self):
        fields = super().get_fields()
        company_id = getattr(getattr(self.context.get("request"), "user", None), "company_id", None)
        fields["extension"].queryset = PbxExtension.objects.filter(company_id=company_id)
        return fields


def validate_members(value):
    if len(value) > 100:
        raise serializers.ValidationError("A queue has at most 100 members.")
    ids = [m["extension"].pk for m in value]
    if len(set(ids)) != len(ids):
        raise serializers.ValidationError("Each extension can be a member once.")
    return value


def save_members(queue, members):
    queue.members.all().delete()
    PbxQueueMember.objects.bulk_create([
        PbxQueueMember(company_id=queue.company_id, queue=queue, extension=m["extension"], penalty=m.get("penalty", 0))
        for m in members
    ])


class PbxQueueSerializer(CompanyScopedSerializerMixin, serializers.ModelSerializer):
    scoped_fields = {"overflow_extension": PbxExtension, "overflow_ivr": PbxIvrMenu}
    members = _QueueMemberSerializer(many=True, required=False)

    class Meta:
        model = PbxQueue
        fields = [
            "id", "name", "strategy", "member_timeout_s", "max_wait_s", "wrapup_s",
            "overflow_type", "overflow_extension", "overflow_ivr", "members", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        return _unique_name(self, PbxQueue, value, "A queue with this name already exists.")

    def validate_member_timeout_s(self, value):
        if not 5 <= value <= 120:
            raise serializers.ValidationError("Ring each agent for 5 to 120 seconds.")
        return value

    def validate_max_wait_s(self, value):
        if not 10 <= value <= 3600:
            raise serializers.ValidationError("Callers wait between 10 seconds and 1 hour.")
        return value

    def validate_wrapup_s(self, value):
        if value > 600:
            raise serializers.ValidationError("Wrap-up time is at most 10 minutes.")
        return value

    def validate_members(self, value):
        return validate_members(value)

    def validate(self, attrs):
        validate_destination(attrs, self.instance, "overflow", queue=False)
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["members"] = [
            {"extension": str(m.extension_id), "number": m.extension.number, "penalty": m.penalty}
            for m in instance.members.select_related("extension")
        ]
        return data

    def create(self, validated_data):
        members = validated_data.pop("members", [])
        queue = super().create(validated_data)
        save_members(queue, members)
        return queue

    def update(self, instance, validated_data):
        members = validated_data.pop("members", None)
        queue = super().update(instance, validated_data)
        if members is not None:
            save_members(queue, members)
        return queue


class QueueMembersSerializer(serializers.Serializer):
    """PUT /queues/{id}/members/ (spec section 6): replace the membership."""

    members = _QueueMemberSerializer(many=True)

    def validate_members(self, value):
        return validate_members(value)


class PbxGatewaySerializer(serializers.ModelSerializer):
    """A GSM gateway: its WireGuard peer plus the trunk it is identified as."""

    name = serializers.CharField(source="trunk.name", read_only=True)
    trunk_status = serializers.CharField(source="trunk.status", read_only=True)
    health = serializers.CharField(source="trunk.health", read_only=True)
    health_reason = serializers.CharField(source="trunk.health_reason", read_only=True)
    health_changed_at = serializers.DateTimeField(source="trunk.health_changed_at", read_only=True)

    class Meta:
        model = PbxGatewayPeer
        fields = [
            "id", "name", "trunk", "tunnel_ip", "status", "public_key", "token_expires_at", "enrolled_at",
            "verified_at", "trunk_status", "health", "health_reason", "health_changed_at", "created_at",
        ]
        read_only_fields = fields


class PbxGatewayCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80)

    def validate_name(self, value):
        value = (value or "").strip()
        company_id = self.context["request"].user.company_id
        if PbxTrunk.objects.filter(company_id=company_id, name=value).exists():
            raise serializers.ValidationError("A trunk with this name already exists.")
        return value


class TelephonyStatusSerializer(serializers.ModelSerializer):
    tag = serializers.CharField(read_only=True)

    class Meta:
        model = PbxTenant
        fields = [
            "tag", "status", "outbound_suspended", "suspended_reason", "config_version",
            "applied_version", "last_apply_error", "recording_mode", "created_at", "updated_at",
        ]


class TelephonySettingsSerializer(serializers.ModelSerializer):
    """
    Tenant-wide PBX settings. Turning recording on requires the admin to attest
    that the company has a legal basis to record and informs callers
    (``accept_recording_terms``); who attested and when is stored and audited.
    """

    accept_recording_terms = serializers.BooleanField(write_only=True, required=False, default=False)
    recording_bytes_used = serializers.SerializerMethodField()
    recording_bytes_limit = serializers.SerializerMethodField()
    # False until the platform enables recording (TELEPHONY_RECORDING_ALLOWED, the legal gate).
    recording_available = serializers.SerializerMethodField()

    class Meta:
        model = PbxTenant
        fields = [
            "recording_mode", "recording_notice", "recording_accepted_at", "recording_accepted_by",
            "accept_recording_terms", "recording_retention_days", "recording_legal_hold", "recording_paused_quota",
            "recording_bytes_used", "recording_bytes_limit", "recording_available",
        ]
        # Legal hold is the platform operator's; the quota pause follows storage use.
        read_only_fields = ["recording_accepted_at", "recording_accepted_by", "recording_legal_hold",
                            "recording_paused_quota"]

    def get_recording_bytes_used(self, obj):
        from .services.recordings import storage_used

        return storage_used(obj.company_id)

    def get_recording_bytes_limit(self, obj):
        from .services.recordings import storage_limit

        return storage_limit(obj.company)

    def get_recording_available(self, obj):
        return bool(settings.TELEPHONY_RECORDING_ALLOWED)

    def validate_recording_retention_days(self, value):
        if not 7 <= value <= 3650:
            raise serializers.ValidationError("Keep recordings between 7 and 3650 days.")
        return value

    def validate(self, attrs):
        from django.utils import timezone

        from .models import RecordingMode

        accepted = attrs.pop("accept_recording_terms", False)
        was_off = self.instance.recording_mode == RecordingMode.OFF
        recording_on = attrs.get("recording_mode", self.instance.recording_mode) != RecordingMode.OFF
        if recording_on and not settings.TELEPHONY_RECORDING_ALLOWED:
            raise serializers.ValidationError(
                {"recording_mode": "Call recording is not available yet."}, code="pbx_recording_unavailable"
            )
        if recording_on and was_off:
            if not accepted:
                raise serializers.ValidationError({
                    "accept_recording_terms": "Confirm that your company may record calls and will inform callers."
                })
            attrs["recording_accepted_at"] = timezone.now()
            attrs["recording_accepted_by"] = self.context["request"].user
        return attrs


# ---- platform admin --------------------------------------------------------


class PbxNodeSerializer(serializers.ModelSerializer):
    tenant_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PbxNode
        fields = [
            "id", "name", "region", "control_url", "status", "max_tenants", "tenant_count",
            "gateway_wg_public_key", "gateway_wg_endpoint", "gateway_wg_address",
            "last_seen_at", "asterisk_version", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "tenant_count", "last_seen_at", "asterisk_version", "created_at", "updated_at"]

    def validate_control_url(self, value):
        value = (value or "").strip()
        if value and not re.fullmatch(r"https?://[^\s/]+(:[0-9]{1,5})?/?", value):
            raise serializers.ValidationError("Enter the worker's address, e.g. http://10.77.0.2:7070.")
        return value.rstrip("/")

    def validate_max_tenants(self, value):
        if not 1 <= value <= 10000:
            raise serializers.ValidationError("A node holds between 1 and 10000 companies.")
        return value

    def validate_gateway_wg_public_key(self, value):
        import base64
        import binascii

        value = (value or "").strip()
        if value:
            try:
                ok = len(value) == 44 and len(base64.b64decode(value, validate=True)) == 32
            except (binascii.Error, ValueError):
                ok = False
            if not ok:
                raise serializers.ValidationError("Enter the node's WireGuard public key (44 characters).")
        return value

    def validate_gateway_wg_endpoint(self, value):
        value = (value or "").strip()
        if value and not re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]{1,5}", value):
            raise serializers.ValidationError("Enter host:port, e.g. pbx-1.example.com:51821.")
        return value


class AdminTenantSerializer(serializers.ModelSerializer):
    tag = serializers.CharField(read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    home_node_name = serializers.CharField(source="home_node.name", read_only=True, default=None)
    draining_node_name = serializers.CharField(source="draining_node.name", read_only=True, default=None)
    effective_fraud_limits = serializers.SerializerMethodField()
    open_fraud_alerts = serializers.SerializerMethodField()

    class Meta:
        model = PbxTenant
        fields = [
            "id", "company", "company_name", "tag", "status", "home_node", "home_node_name",
            "draining_node", "draining_node_name", "outbound_suspended", "outbound_blocked_on_node",
            "suspended_reason", "config_version", "applied_version", "last_apply_error",
            "recording_mode", "recording_retention_days", "recording_legal_hold", "recording_paused_quota",
            "fraud_limits", "effective_fraud_limits", "open_fraud_alerts", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_effective_fraud_limits(self, obj):
        from .services.fraud import effective_limits

        return effective_limits(obj)

    def get_open_fraud_alerts(self, obj):
        return obj.fraud_alerts.filter(resolved_at__isnull=True).count()


class AdminTenantUpdateSerializer(serializers.ModelSerializer):
    """What the platform operator sets on a tenant: fraud limit overrides and the recordings legal hold."""

    class Meta:
        model = PbxTenant
        fields = ["fraud_limits", "recording_legal_hold"]

    def validate_fraud_limits(self, value):
        from .services.fraud import InvalidLimits, validate_limits

        try:
            return validate_limits(value or {})
        except InvalidLimits as exc:
            raise serializers.ValidationError(str(exc))


class AdminNumberSerializer(serializers.ModelSerializer):
    """The number verification queue: every tenant's DIDs, with what the operator needs to check ownership."""

    verified = serializers.SerializerMethodField()
    company = serializers.IntegerField(source="company_id", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    trunk_name = serializers.CharField(source="trunk.name", read_only=True)
    trunk_kind = serializers.CharField(source="trunk.kind", read_only=True)

    class Meta:
        model = PbxNumber
        fields = ["id", "e164", "label", "company", "company_name", "trunk", "trunk_name", "trunk_kind", "verified",
                  "verified_at", "created_at"]
        read_only_fields = fields

    def get_verified(self, obj):
        return obj.verified_at is not None


class AdminFraudAlertSerializer(serializers.ModelSerializer):
    company = serializers.IntegerField(source="company_id", read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    tenant_outbound_suspended = serializers.BooleanField(source="tenant.outbound_suspended", read_only=True)
    resolved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PbxFraudAlert
        fields = ["id", "company", "company_name", "rule", "severity", "action_taken", "observed", "threshold",
                  "destination", "occurred_at", "resolved_at", "resolved_by", "resolved_by_name", "resolution_note",
                  "tenant_outbound_suspended"]
        read_only_fields = fields

    def get_resolved_by_name(self, obj):
        user = obj.resolved_by
        return (user.get_full_name() or user.username) if user else None


class PbxJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = PbxProvisioningJob
        fields = ["id", "kind", "status", "target_version", "attempts", "last_error", "created_at", "finished_at"]
        read_only_fields = fields


class ApplyResultSerializer(serializers.Serializer):
    """Mirror of CRM-pbx/contract/schemas/apply-result.schema.json."""

    tenant = serializers.RegexField(contract.TENANT_TAG_RE)
    version = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=["applied", "failed", "rolled_back"])
    running_version = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    error = serializers.DictField(required=False)
    finished_at = serializers.DateTimeField()
    asterisk_version = serializers.CharField(max_length=40, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["status"] == "applied" and attrs.get("error"):
            raise serializers.ValidationError("applied results carry no error")
        if attrs["status"] != "applied" and not attrs.get("error"):
            raise serializers.ValidationError("failed results must carry an error")
        return attrs


# ---- calls (Phase 2) ---------------------------------------------------------


class PbxRecordingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PbxRecording
        fields = ["id", "call", "duration_s", "format", "size_bytes", "started_at", "created_at"]
        read_only_fields = fields


class PbxCallLegSerializer(serializers.ModelSerializer):
    extension_number = serializers.CharField(source="extension.number", read_only=True, default=None)

    class Meta:
        model = PbxCallLeg
        fields = [
            "id", "endpoint", "extension", "extension_number", "source", "destination", "disposition",
            "started_at", "answered_at", "ended_at",
        ]
        read_only_fields = fields


class PbxCallSerializer(serializers.ModelSerializer):
    extension_number = serializers.CharField(source="extension.number", read_only=True, default=None)
    answered_by_number = serializers.CharField(source="answered_by.number", read_only=True, default=None)
    crm_contact_name = serializers.CharField(source="crm_contact.name", read_only=True, default=None)
    has_recording = serializers.SerializerMethodField()

    class Meta:
        model = PbxCall
        fields = [
            "id", "direction", "origin", "status", "from_party", "to_party", "did",
            "extension", "extension_number", "answered_by", "answered_by_number",
            "crm_contact", "crm_contact_name", "on_hold", "hangup_cause", "duration_s", "billsec_s",
            "recorded", "has_recording", "started_at", "answered_at", "ended_at",
        ]
        read_only_fields = fields

    def get_has_recording(self, obj):
        return obj.recordings.exists()


class PbxCallDetailSerializer(PbxCallSerializer):
    legs = PbxCallLegSerializer(many=True, read_only=True)
    recordings = PbxRecordingSerializer(many=True, read_only=True)
    transfers = serializers.SerializerMethodField()

    class Meta(PbxCallSerializer.Meta):
        fields = PbxCallSerializer.Meta.fields + ["legs", "recordings", "transfers"]
        read_only_fields = fields

    def get_transfers(self, obj):
        """REFER transfers the node reported (call.transferred), oldest first."""
        out = []
        for ev in obj.events.filter(type="call.transferred").order_by("occurred_at", "seq"):
            data = ev.data or {}
            out.append({
                "mode": data.get("mode"),
                "from_extension": data.get("from_extension") or None,
                "to_extension": data.get("to_extension") or None,
                "to": data.get("to") or None,
                "occurred_at": ev.occurred_at,
            })
        return out


class PbxCallEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PbxCallEvent
        fields = ["id", "type", "call", "occurred_at", "data", "received_at"]
        read_only_fields = fields
