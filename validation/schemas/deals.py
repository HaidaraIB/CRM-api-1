from validation.registry import register_form
from validation.schema_dsl import date_field, integer_field, max_length, number_field, required, rule, string_field


def _load():
    register_form(
        "deal.upsert",
        {
            "client": integer_field("client", required(), sample=1),
            # Deal.title is genuinely optional server-side (model default="",
            # falls back to the client's name) — never make this required.
            "title": string_field("title", max_length(255), sample="Deal"),
            "value": number_field(
                "value",
                rule("number_range", {"min": 0}, client_only=True),
                rule("decimal_places", {"max": 2}, client_only=True),
                sample=100,
            ),
            "probability": integer_field(
                "probability",
                rule("integer"),
                rule("number_range", {"min": 0, "max": 100}),
                sample=50,
            ),
            "discount_percentage": number_field(
                "discount",
                rule("number_range", {"min": 0, "max": 100}),
                sample=0,
            ),
            "expected_close_date": date_field("expectedCloseDate"),
            "start_date": date_field("startDate"),
            "closed_date": date_field(
                "closedDate",
                rule("date_range", {"gte_field": "start_date"}, client_only=True),
            ),
            "description": string_field("description", max_length(5000), sample="Details"),
            "currency": string_field("currency", max_length(10), sample="USD"),
        },
        aliases={"expectedCloseDate": "expected_close_date", "startDate": "start_date", "closedDate": "closed_date"},
    )
    register_form(
        "deal.close",
        {
            "status": string_field(
                "status",
                required(),
                rule("one_of", {"values": ["won", "lost"]}, client_only=True),
                sample="won",
            ),
            "lost_reason": integer_field(
                "lostReason",
                rule("required_if", {"field": "status", "equals": "lost"}, client_only=True),
                ui_key="lostReason",
                sample=1,
            ),
            "lost_note": string_field("lostNote", max_length(2000), ui_key="lostNote", sample="Note"),
        },
        aliases={"lostReason": "lost_reason", "lostNote": "lost_note"},
    )


_load()
