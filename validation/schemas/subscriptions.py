from validation.registry import register_form
from validation.schema_dsl import integer_field, max_length, name_field, number_field, required, rule, string_field


def _load():
    register_form(
        "plan.upsert",
        {
            "name": name_field(),
            "name_ar": name_field(label_key="nameAr", client_required=True),
            "description": string_field("description", max_length(5000), sample="Plan details"),
            "description_ar": string_field("descriptionAr", max_length(5000), sample="تفاصيل"),
            "monthly_price": number_field("monthlyPrice", rule("number_range", {"min": 0}, client_only=True), sample=10),
            "yearly_price": number_field("yearlyPrice", rule("number_range", {"min": 0}, client_only=True), sample=100),
            "trial_days": integer_field(
                "trialDays",
                rule("integer"),
                rule("number_range", {"min": 0, "max": 365}, client_only=True),
                sample=14,
            ),
        },
        aliases={
            "nameAr": "name_ar",
            "descriptionAr": "description_ar",
            "monthlyPrice": "monthly_price",
            "yearlyPrice": "yearly_price",
            "trialDays": "trial_days",
        },
    )
    register_form(
        "payment_gateway.create",
        {
            # PaymentGateway (subscriptions/models.py) has no separate "provider"
            # field — `name` itself is the gateway identifier the admin panel
            # fuzzy-matches (substring, case-insensitive) to brand it, e.g.
            # "Al Qaseh"/"alqaseh"/"al-qaseh" are all the same gateway. Free-form
            # display name, unique — no one_of restriction.
            "name": string_field("provider", required(), max_length(255), sample="Stripe"),
        },
    )
    register_form(
        "trial_code.create",
        {
            # `code` is optional here: a blank code means "auto-generate one" —
            # see TrialCodeCreateSerializer.validate_code. When a code IS
            # supplied it must match the format the generator itself uses.
            "code": string_field(
                "code",
                rule("pattern", {"regex": r"^[A-Z0-9_-]{4,32}$"}),
                sample="TRIAL2026",
            ),
            "trial_days": integer_field(
                "trialDays",
                required(),
                rule("number_range", {"min": 1, "max": 365}),
                sample=14,
            ),
        },
        aliases={"trialDays": "trial_days"},
    )
    register_form(
        "trial_code.batch",
        {
            "label": name_field(),
            "quantity": integer_field(
                "count",
                required(),
                rule("number_range", {"min": 1, "max": 500}),
                sample=10,
            ),
            "trial_days": integer_field(
                "trialDays",
                required(),
                rule("number_range", {"min": 1, "max": 365}),
                sample=14,
            ),
            "plan": integer_field("plan", required(client_only=True), sample=1),
        },
        aliases={"trialDays": "trial_days"},
    )
    register_form(
        "trial_code.redeem",
        {"code": string_field("code", required(), max_length(64), sample="TRIAL2026")},
        public=True,
    )
    register_form(
        "broadcast.create",
        {
            "subject": string_field("title", required(), max_length(255), ui_key="title", sample="Announcement"),
            "content": string_field("message", required(), ui_key="body", sample="Hello"),
        },
        aliases={"title": "subject", "body": "content", "message": "content"},
    )


_load()
