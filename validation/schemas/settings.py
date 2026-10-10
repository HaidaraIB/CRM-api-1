from validation.registry import register_form
from validation.schema_dsl import integer_field, max_length, name_field, required, rule, string_field


def _named(form_id: str, *, color: bool = True, description: bool = True):
    fields = {"name": name_field()}
    if description:
        fields["description"] = string_field("description", max_length(2000), sample="Details")
    if color:
        fields["color"] = string_field(
            "color",
            rule("pattern", {"regex": r"#[0-9A-Fa-f]{6}"}, client_only=True),
            sample="#336699",
        )
    register_form(form_id, fields)


def _load():
    _named("channel.upsert", color=False)
    _named("stage.upsert")
    _named("status.upsert")
    _named("call_method.upsert")
    _named("visit_type.upsert")
    _named("tag.upsert")
    _named("deal_pipeline.upsert")
    _named("deal_lost_reason.upsert")
    register_form(
        "deal_stage.upsert",
        {
            "name": name_field(),
            "description": string_field("description", max_length(2000), sample="Details"),
            "color": string_field(
                "color",
                rule("pattern", {"regex": r"#[0-9A-Fa-f]{6}"}, client_only=True),
                sample="#336699",
            ),
            # Both have model-level defaults (stage_type="open", probability=50),
            # so they're only validated when actually present in the payload —
            # not required().
            "stage_type": string_field(
                "stageType",
                rule("one_of", {"values": ["open", "won", "lost"]}),
                sample="open",
            ),
            "probability": integer_field(
                "probability",
                rule("integer"),
                rule("number_range", {"min": 0, "max": 100}),
                sample=50,
            ),
        },
        aliases={"stageType": "stage_type"},
    )
    register_form(
        # Client-side only — companies/views.py `update_assignment_settings` is a
        # plain `@action`, not a catalog-bound serializer; it already enforces
        # these same ranges itself (invalid_re_assign_hours/invalid_no_follow_up_hours).
        "company_assignment_settings.update",
        {
            "re_assign_hours": integer_field(
                "reminderDelayTime", rule("integer"), rule("number_range", {"min": 1}), ui_key="reAssignHours", sample=24
            ),
            "no_follow_up_hours": integer_field(
                "noFollowUpHours", rule("integer"), rule("number_range", {"min": 1, "max": 168}), ui_key="noFollowUpHours", sample=10
            ),
            "no_follow_up_digest_hour": integer_field(
                "noFollowUpDigestHour",
                rule("integer"),
                rule("number_range", {"min": 0, "max": 23}),
                ui_key="noFollowUpDigestHour",
                sample=9,
            ),
        },
        aliases={"reAssignHours": "re_assign_hours", "noFollowUpHours": "no_follow_up_hours", "noFollowUpDigestHour": "no_follow_up_digest_hour"},
    )
    register_form(
        "system_settings.update",
        {
            "login_max_failed_attempts": integer_field(
                "loginMaxFailedAttempts",
                rule("integer"),
                rule("number_range", {"min": 1}),
                sample=5,
            ),
            "login_lockout_duration_minutes": integer_field(
                "loginLockoutDurationMinutes",
                rule("integer"),
                rule("number_range", {"min": 1}),
                sample=15,
            ),
        },
        aliases={
            "loginMaxFailedAttempts": "login_max_failed_attempts",
            "loginLockoutDurationMinutes": "login_lockout_duration_minutes",
        },
    )
    register_form(
        "work_hours.update",
        {
            "idle_timeout_minutes": integer_field(
                "idleTimeout",
                required(),
                rule("integer"),
                rule("number_range", {"min": 1, "max": 120}),
                sample=15,
            )
        },
        aliases={"idleTimeoutMinutes": "idle_timeout_minutes"},
    )
    register_form(
        "billing_settings.update",
        {
            # BillingSettings.issuer_name/issuer_email are `blank=True, default=""`
            # (settings/models.py) — genuinely optional server-side.
            "issuer_name": name_field(label_key="issuerName", client_required=True),
            "issuer_email": string_field("issuerEmail", required(client_only=True), rule("email"), sample="billing@example.com"),
            "logo": string_field(
                "logo",
                rule("file", {"max_bytes": 2_000_000, "mime": ["image/png", "image/jpeg", "image/webp"]}, client_only=True),
                sample="",
            ),
        },
        aliases={"issuerName": "issuer_name", "issuerEmail": "issuer_email"},
    )


_load()
