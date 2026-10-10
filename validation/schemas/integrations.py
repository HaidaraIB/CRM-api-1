from validation.custom_rules import whatsapp_template_patterns
from validation.registry import register_form
from validation.schema_dsl import max_length, required, rule, string_field


def _load():
    register_form(
        "twilio_settings.update",
        {
            # TwilioSettings.account_sid/twilio_number/auth_token are all
            # `blank=True, null=True` (integrations/models.py), and
            # TwilioSettingsSerializer explicitly makes auth_token
            # `required=False` so updating other fields doesn't force
            # re-submitting the secret — server never enforces these either way.
            # (Also: the real field is `twilio_number`, not `from_number`.)
            "account_sid": string_field("accountSid", required(client_only=True), max_length(64), sample="AC" + "1" * 32),
            "auth_token": string_field("authToken", required(client_only=True), max_length(128), sample="token-value"),
            "twilio_number": string_field(
                "fromNumber", required(client_only=True), rule("phone_e164"), ui_key="fromNumber", sample="+15551234567"
            ),
        },
        aliases={"accountSid": "account_sid", "authToken": "auth_token", "fromNumber": "twilio_number"},
    )
    register_form(
        "sms.send",
        {
            "to": string_field("phone", required(client_only=True), rule("phone_e164", client_only=True), sample="+15551234567"),
            "body": string_field("message", required(client_only=True), max_length(1000), sample="Hello"),
        },
    )
    register_form(
        "whatsapp_template.upsert",
        {
            "name": string_field(
                "name",
                required(),
                rule("pattern", {"regex": r"[A-Za-z0-9_ \-]+"}, client_only=True),
                max_length(255),
                sample="welcome_message",
            ),
            "content": string_field(
                "body",
                required(client_only=True),
                rule(
                    "custom:whatsapp_template_body",
                    {"patterns": whatsapp_template_patterns(), "min_static_words_per_var": 3},
                    client_only=True,
                ),
                max_length(5000),
                ui_key="body",
                sample="Hello, thanks for contacting us today.",
            ),
            # MessageTemplate.language is `blank=True, default='en_US'`.
            "language": string_field("language", required(client_only=True), max_length(16), sample="en"),
        },
    )
    register_form(
        "whatsapp_chat.start_conversation",
        {
            "phone": string_field("phone", required(), rule("phone_e164"), sample="+15551234567"),
        },
    )
    register_form(
        "openai_settings.update",
        {
            # OpenAISettingsSerializer makes api_key `required=False` so updating
            # other settings doesn't force re-submitting the secret — server
            # never enforces this either way.
            "api_key": string_field("apiKey", required(client_only=True), max_length(256), sample="sk-test-key"),
            "model": string_field("model", max_length(64), sample="gpt-4o-mini"),
        },
        aliases={"apiKey": "api_key"},
    )


_load()
