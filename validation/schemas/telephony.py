"""
Loop Cloud PBX settings forms (CRM-project Settings > Phone System).

The serializers in telephony/serializers.py stay the authority on what a value
means (company scoping, uniqueness, E.164 normalisation, destination types); these
rules are what both sides can check without the database, so the UI shows the
same errors before it submits. Ranges match the serializers exactly.
"""

from validation.registry import register_form
from validation.schema_dsl import (
    array_field,
    boolean_field,
    integer_field,
    max_length,
    min_length,
    object_field,
    required,
    rule,
    string_field,
)

UUID_SAMPLE = "1a2b3c4d-0000-4000-8000-000000000001"
DESTINATIONS = ["extension", "ivr", "queue", "hangup"]


def _name(label="pbxName"):
    return string_field(label, required(), max_length(80), sample="Sales")


def _one_of(label, values, sample, *, req=False):
    rules = [required()] if req else []
    return string_field(label, *rules, rule("one_of", {"values": values}), sample=sample)


def _target(label, type_field, kind, sample=None):
    """A destination target: required when the destination type is ``kind``."""
    return string_field(label, rule("required_if", {"field": type_field, "equals": kind}), sample=sample)


def _range(label, lo, hi, sample):
    return integer_field(label, rule("integer"), rule("number_range", {"min": lo, "max": hi}), sample=sample)


def _load():
    register_form(
        "pbx_extension.upsert",
        {
            "number": string_field(
                "pbxExtensionNumber", required(), rule("pattern", {"regex": r"[1-9][0-9]{1,5}"}), sample="101"
            ),
            "display_name": string_field("pbxDisplayName", max_length(80), sample="Front desk"),
            "device_type": _one_of("pbxDeviceType", ["webrtc", "desk_phone", "softphone"], "webrtc"),
            "outbound_number": string_field("pbxOutboundSim"),
        },
    )
    register_form(
        "pbx_trunk.upsert",
        {
            "name": _name("pbxTrunkName"),
            "kind": _one_of("pbxTrunkKind", ["sip", "gsm_gateway"], "sip"),
            "host": string_field("pbxTrunkHost", required(), max_length(253), sample="sip.example.com"),
            "port": _range("pbxTrunkPort", 1, 65535, 5060),
            "transport": _one_of("pbxTrunkTransport", ["udp", "tcp", "tls"], "udp"),
            "auth_username": string_field(
                "pbxTrunkAuthUsername", max_length(80), rule("pattern", {"regex": r"[A-Za-z0-9_.+-]*"}), sample="loop"
            ),
            "auth_secret": string_field(
                "pbxTrunkAuthSecret", min_length(16), max_length(128), rule("pattern", {"regex": r"[A-Za-z0-9_-]*"}),
                sample="abcdefghijklmnop",
            ),
            "allowed_sources": array_field(
                "pbxTrunkAllowedSources", string_field("pbxTrunkAllowedSources"), rule("array", {"max": 16})
            ),
        },
    )
    register_form("pbx_gateway.create", {"name": _name("pbxGatewayName")})
    register_form(
        "pbx_number.upsert",
        {
            "e164": string_field(
                "pbxNumberE164", required(), rule("pattern", {"regex": r"\+[1-9][0-9]{6,14}"}), sample="+9647700000001"
            ),
            "label": string_field("pbxNumberLabel", max_length(80), sample="Main line"),
            "trunk": string_field("pbxNumberTrunk", required(), sample=UUID_SAMPLE),
        },
    )
    register_form(
        "pbx_inbound_route.upsert",
        {
            "number": string_field("pbxRouteNumber", required(), sample=UUID_SAMPLE),
            "destination_type": _one_of("pbxDestination", ["extension", "ivr", "queue"], "extension", req=True),
            "extension": _target("pbxDestinationExtension", "destination_type", "extension", UUID_SAMPLE),
            "ivr": _target("pbxDestinationIvr", "destination_type", "ivr"),
            "queue": _target("pbxDestinationQueue", "destination_type", "queue"),
            "business_hours": string_field("pbxBusinessHours"),
            "closed_type": _one_of("pbxClosedDestination", DESTINATIONS, "hangup"),
            "closed_extension": _target("pbxDestinationExtension", "closed_type", "extension"),
            "closed_ivr": _target("pbxDestinationIvr", "closed_type", "ivr"),
            "closed_queue": _target("pbxDestinationQueue", "closed_type", "queue"),
        },
    )
    register_form(
        "pbx_outbound_route.upsert",
        {
            "name": _name("pbxRouteName"),
            "priority": _range("pbxRoutePriority", 0, 9999, 100),
            "patterns": array_field(
                "pbxRoutePatterns", object_field("pbxRoutePatterns", {}), required(), rule("array", {"min": 1, "max": 50})
            ),
            "trunk": string_field("pbxNumberTrunk", required(), sample=UUID_SAMPLE),
            "caller_id": string_field("pbxRouteCallerId", required(), sample=UUID_SAMPLE),
            "enabled": boolean_field("pbxRouteEnabled"),
        },
    )
    register_form(
        "pbx_settings.update",
        {
            "recording_mode": _one_of("pbxRecordingMode", ["off", "external", "all"], "off"),
            "recording_notice": _one_of("pbxRecordingNotice", ["none", "ar", "en", "ar_en"], "ar_en"),
            "accept_recording_terms": boolean_field("pbxRecordingTerms"),
            "recording_retention_days": _range("pbxRecordingRetention", 7, 3650, 90),
        },
    )
    register_form(
        "pbx_business_hours.upsert",
        {
            "name": _name("pbxHoursName"),
            "timezone": string_field("pbxTimezone", required(), max_length(64), sample="Asia/Baghdad"),
            "rules": array_field(
                "pbxHoursRules", object_field("pbxHoursRules", {}), required(), rule("array", {"min": 1, "max": 14})
            ),
            "closed_dates": array_field("pbxClosedDates", string_field("pbxClosedDates"), rule("array", {"max": 100})),
        },
    )
    register_form(
        "pbx_ivr.upsert",
        {
            "name": _name("pbxIvrName"),
            "prompt": _one_of("pbxIvrPrompt", ["loop/ivr-default", "loop/ivr-welcome-ar", "loop/ivr-welcome-en"],
                              "loop/ivr-default"),
            "greeting": string_field("pbxIvrGreeting"),
            "timeout_s": _range("pbxIvrTimeout", 1, 30, 5),
            "max_retries": _range("pbxIvrRetries", 0, 5, 2),
            "timeout_type": _one_of("pbxIvrTimeoutDestination", DESTINATIONS, "hangup"),
            "timeout_extension": _target("pbxDestinationExtension", "timeout_type", "extension"),
            "timeout_ivr": _target("pbxDestinationIvr", "timeout_type", "ivr"),
            "timeout_queue": _target("pbxDestinationQueue", "timeout_type", "queue"),
            "options": array_field("pbxIvrOptions", object_field("pbxIvrOptions", {}), rule("array", {"max": 12})),
        },
    )
    register_form(
        "pbx_sound.upload",
        {
            "name": _name("pbxSoundName"),
            # The browser converts the pick to WAV (16-bit PCM, mono, 8 kHz) before
            # upload; the server checks that format and the 0.5-60 s length.
            "file": string_field(
                "pbxSoundFile",
                required(client_only=True),
                rule("file", {"max_bytes": 1_000_000, "mime": ["audio/wav", "audio/x-wav", "audio/wave"]}, client_only=True),
                sample="greeting.wav",
            ),
        },
    )
    register_form("pbx_sound.update", {"name": _name("pbxSoundName")})
    # ---- platform console (CRM-admin-panel > Telephony) ----
    register_form(
        "pbx_node.upsert",
        {
            "name": string_field("pbxNodeName", required(), max_length(64), sample="pbx-1"),
            "region": string_field("pbxNodeRegion", max_length(64), sample="iq-bgw"),
            "control_url": string_field(
                "pbxNodeControlUrl", max_length(200), rule("pattern", {"regex": r"(https?://[^\s/]+(:[0-9]{1,5})?/?)?"}),
                sample="http://10.77.0.2:7070",
            ),
            "status": _one_of("pbxNodeStatus", ["active", "draining", "offline"], "active"),
            "max_tenants": _range("pbxNodeMaxTenants", 1, 10000, 100),
            "gateway_wg_public_key": string_field(
                "pbxNodeGatewayKey", rule("pattern", {"regex": r"([A-Za-z0-9+/]{43}=)?"}), sample="",
            ),
            "gateway_wg_endpoint": string_field(
                "pbxNodeGatewayEndpoint", max_length(200), rule("pattern", {"regex": r"([A-Za-z0-9.-]+:[0-9]{1,5})?"}),
                sample="pbx-1.example.com:51821",
            ),
            "gateway_wg_address": string_field(
                "pbxNodeGatewayAddress",
                rule("pattern", {"regex": r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"}), sample="10.78.0.1",
            ),
        },
    )
    register_form(
        "pbx_tenant.admin_update",
        {
            "fraud_limits": object_field("pbxFraudLimits", {}),
            "recording_legal_hold": boolean_field("pbxLegalHold"),
        },
    )
    register_form("pbx_tenant.suspend", {"reason": string_field("pbxSuspendReason", max_length(200), sample="Unpaid")})
    register_form("pbx_tenant.migrate", {"node": string_field("pbxMigrateNode", required(), sample=UUID_SAMPLE)})
    register_form("pbx_fraud_alert.resolve", {"note": string_field("pbxResolveNote", max_length(500), sample="Checked")})
    register_form(
        "pbx_queue.upsert",
        {
            "name": _name("pbxQueueName"),
            "strategy": _one_of("pbxQueueStrategy", ["ringall", "rrmemory", "leastrecent", "fewestcalls", "random"],
                                "ringall"),
            "member_timeout_s": _range("pbxQueueMemberTimeout", 5, 120, 20),
            "max_wait_s": _range("pbxQueueMaxWait", 10, 3600, 300),
            "wrapup_s": _range("pbxQueueWrapup", 0, 600, 0),
            "overflow_type": _one_of("pbxQueueOverflow", ["extension", "ivr", "hangup"], "hangup"),
            "overflow_extension": _target("pbxDestinationExtension", "overflow_type", "extension"),
            "overflow_ivr": _target("pbxDestinationIvr", "overflow_type", "ivr"),
            "members": array_field("pbxQueueMembers", object_field("pbxQueueMembers", {}), rule("array", {"max": 100})),
        },
    )


_load()
