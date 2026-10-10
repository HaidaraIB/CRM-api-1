from validation.registry import register_form
from validation.schema_dsl import (
    email_field,
    max_length,
    name_field,
    object_field,
    password_field,
    phone_field,
    required,
    rule,
    string_field,
)

_SPECIALIZATIONS = ["real_estate", "services", "products", "medical"]


def _load():
    register_form(
        "tenant.upsert",
        {
            "name": name_field(max_=64),
            "domain": string_field("companyDomain", required(), max_length(256), sample="acme"),
            # Company.specialization has a model default — DRF already treats it
            # as optional; keep `required(client_only=True)` for the client hint.
            "specialization": string_field(
                "specialization",
                required(client_only=True),
                rule("one_of", {"values": _SPECIALIZATIONS}),
                sample="real_estate",
            ),
        },
    )
    register_form(
        "tenant.create",
        {
            "company": object_field(
                "company",
                {
                    "name": name_field(max_=64),
                    "domain": string_field("companyDomain", required(), max_length(256), sample="acme"),
                    "specialization": string_field(
                        "specialization",
                        required(),
                        rule("one_of", {"values": _SPECIALIZATIONS}),
                        sample="real_estate",
                    ),
                },
                required(),
            ),
            "owner": object_field(
                "owner",
                {
                    "first_name": name_field(label_key="firstName"),
                    "last_name": name_field(label_key="lastName"),
                    "email": email_field(),
                    "username": string_field("username", required(), rule("username", client_only=True)),
                    "password": password_field(),
                    "phone": phone_field(client_required=False),
                },
                required(),
            ),
        },
    )
    register_form(
        "demo_booking.create",
        {
            "name": name_field(),
            "email": email_field(),
            "phone": phone_field(client_required=False),
            "company_name": string_field("company", max_length(255), sample="Acme"),
            "starts_at": string_field(
                "date",
                required(client_only=True),
                ui_key="scheduledAt",
                sample="2026-10-09T10:00:00Z",
            ),
        },
        public=True,
        aliases={"companyName": "company_name", "scheduledAt": "scheduled_at"},
    )


_load()
