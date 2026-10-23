from validation.registry import register_form
from validation.schema_dsl import (
    array_field,
    boolean_field,
    date_field,
    integer_field,
    max_length,
    name_field,
    number_field,
    object_field,
    required,
    rule,
    string_field,
)

PLAN_BADGES = ["", "most_popular", "best_value", "recommended", "new", "limited_offer", "custom"]
PROMOTION_THEMES = ["sale", "brand", "dark", "amber"]
PROMOTION_AUDIENCES = ["public", "dashboard", "mobile"]



def _load():
    register_form(
        "plan.upsert",
        {
            "name": name_field(),
            "name_ar": name_field(label_key="nameAr", client_required=True),
            "description": string_field("description", max_length(5000), sample="Plan details"),
            "description_ar": string_field("descriptionAr", max_length(5000), sample="تفاصيل"),
            # Keys are the real model fields so the server actually enforces them
            # (they were `monthly_price`/`yearly_price` and silently never matched).
            "price_monthly": number_field(
                "monthlyPrice", rule("number_range", {"min": 0}, client_only=True), ui_key="monthlyPrice", sample=10
            ),
            "price_yearly": number_field(
                "yearlyPrice", rule("number_range", {"min": 0}, client_only=True), ui_key="yearlyPrice", sample=100
            ),
            "trial_days": integer_field(
                "trialDays",
                rule("integer"),
                rule("number_range", {"min": 0, "max": 365}, client_only=True),
                sample=14,
            ),
            # Sale (cross-field "0 < sale < regular" lives in PlanSerializer).
            "sale_price_monthly": number_field(
                "salePriceMonthly",
                rule("number_range", {"min": 0.01}),
                rule("decimal_places", {"max": 2}),
                ui_key="salePriceMonthly",
                sample=5,
            ),
            "sale_price_yearly": number_field(
                "salePriceYearly",
                rule("number_range", {"min": 0.01}),
                rule("decimal_places", {"max": 2}),
                ui_key="salePriceYearly",
                sample=50,
            ),
            "sale_starts_at": date_field("saleStartsAt", ui_key="saleStartsAt"),
            "sale_ends_at": date_field(
                "saleEndsAt", rule("date_range", {"gte_field": "sale_starts_at"}), ui_key="saleEndsAt"
            ),
            "badge": string_field("badge", rule("one_of", {"values": PLAN_BADGES}), sample="most_popular"),
            "badge_text": string_field(
                "badgeText",
                rule("required_if", {"field": "badge", "equals": "custom"}),
                max_length(40),
                ui_key="badgeText",
                sample="Hot",
            ),
            "badge_text_ar": string_field(
                "badgeTextAr",
                rule("required_if", {"field": "badge", "equals": "custom"}),
                max_length(40),
                ui_key="badgeTextAr",
                sample="\u0631\u0627\u0626\u062c",
            ),
            "offer_note": string_field("offerNote", max_length(160), ui_key="offerNote", sample="2x credits"),
            "offer_note_ar": string_field("offerNoteAr", max_length(160), ui_key="offerNoteAr", sample="Sample"),
            "sort_order": integer_field(
                "sortOrder", rule("integer"), rule("number_range", {"min": 0, "max": 999}), ui_key="sortOrder", sample=0
            ),
        },
        aliases={
            "nameAr": "name_ar",
            "descriptionAr": "description_ar",
            "monthlyPrice": "price_monthly",
            "yearlyPrice": "price_yearly",
            "trialDays": "trial_days",
            "salePriceMonthly": "sale_price_monthly",
            "salePriceYearly": "sale_price_yearly",
            "saleStartsAt": "sale_starts_at",
            "saleEndsAt": "sale_ends_at",
            "badgeText": "badge_text",
            "badgeTextAr": "badge_text_ar",
            "offerNote": "offer_note",
            "offerNoteAr": "offer_note_ar",
            "sortOrder": "sort_order",
        },
    )
    register_form(
        "promotion.upsert",
        {
            "name": name_field(max_=120),
            "messages": array_field(
                "promotionMessages",
                object_field(
                    "promotionMessage",
                    {
                        "en": string_field("messageEn", required(), max_length(120), sample="Biggest deals of the year"),
                        "ar": string_field("messageAr", required(), max_length(120), sample="Sample"),
                    },
                ),
                rule("array", {"min": 1, "max": 6}),
            ),
            "cta_label": string_field("ctaLabel", max_length(40), ui_key="ctaLabel", sample="See plans"),
            "cta_label_ar": string_field("ctaLabelAr", max_length(40), ui_key="ctaLabelAr", sample="Sample"),
            "cta_url": string_field("ctaUrl", max_length(500), ui_key="ctaUrl", sample="/register"),
            "theme": string_field("theme", required(), rule("one_of", {"values": PROMOTION_THEMES}), sample="sale"),
            "starts_at": date_field("startsAt", required(), ui_key="startsAt"),
            "ends_at": date_field("endsAt", rule("date_range", {"gte_field": "starts_at"}), ui_key="endsAt"),
            "show_countdown": boolean_field("showCountdown", ui_key="showCountdown"),
            "audiences": array_field(
                "audiences",
                string_field("audience", rule("one_of", {"values": PROMOTION_AUDIENCES}), sample="public"),
                rule("array", {"min": 1}),
            ),
            "priority": integer_field(
                "priority", rule("integer"), rule("number_range", {"min": -999, "max": 999}), sample=0
            ),
        },
        aliases={
            "ctaLabel": "cta_label",
            "ctaLabelAr": "cta_label_ar",
            "ctaUrl": "cta_url",
            "startsAt": "starts_at",
            "endsAt": "ends_at",
            "showCountdown": "show_countdown",
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
