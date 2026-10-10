from validation.registry import register_form
from validation.schema_dsl import max_length, required, rule, string_field


def _load():
    register_form(
        "support_ticket.create",
        {
            "title": string_field("subject", required(), max_length(255), ui_key="subject", sample="Need help"),
            "description": string_field(
                "message",
                required(),
                rule("max_length", {"max": 8000}, client_only=True),
                ui_key="message",
                sample="Details of the issue",
            ),
            "priority": string_field(
                "priority",
                rule("one_of", {"values": ["low", "medium", "high"]}, client_only=True),
                sample="medium",
            ),
        },
        aliases={"subject": "title", "message": "description"},
    )
    register_form(
        "support_message.send",
        {
            "body": string_field("message", required(client_only=True), max_length(8000), sample="Hello"),
        },
    )


_load()
