from validation.registry import register_form
from validation.schema_dsl import (
    array_field,
    integer_field,
    max_length,
    name_field,
    object_field,
    required,
    rule,
    string_field,
)


def _load():
    phone_item = object_field(
        "phone",
        {
            "phone_number": string_field(
                "phone",
                required(client_only=True),
                rule("phone_e164", client_only=True),
                sample="+15551234567",
            ),
        },
    )
    register_form(
        "lead.upsert",
        {
            "name": name_field(),
            # phone_number/communication_way/status are nullable at the model
            # level (crm/models.py Client) — many real creation paths (inbound
            # webhooks, imports, quick-add) legitimately omit them. They keep
            # `required(client_only=True)` so clients still nudge for them, but
            # the server only ever format-checks them when present.
            "phone_number": string_field(
                "phone",
                required(client_only=True),
                rule("phone_e164"),
                ui_key="phone",
                sample="+15551234567",
            ),
            "phone_numbers": array_field(
                "phone",
                phone_item,
                rule("array", {"min": 1}, client_only=True),
                ui_key="phoneNumbers",
            ),
            "communication_way": integer_field(
                "communicationWay",
                required(client_only=True),
                ui_key="communicationWay",
                sample=1,
            ),
            "status": integer_field("status", required(client_only=True), sample=1),
            "priority": string_field(
                "priority",
                required(client_only=True),
                rule("one_of", {"values": ["low", "medium", "high"]}, client_only=True),
                sample="medium",
            ),
            "type": string_field(
                "type",
                required(client_only=True),
                rule("one_of", {"values": ["fresh", "hot", "cold"]}, client_only=True),
                sample="fresh",
            ),
            "company": integer_field(
                "company",
                required(when="requireCompany", client_only=True),
                ui_key="companyId",
                sample=1,
            ),
            "email": string_field("email", rule("email"), max_length(254), sample="user@example.com"),
            "lead_company_name": string_field(
                "leadCompanyName",
                max_length(255),
                ui_key="leadCompanyName",
                sample="Acme",
            ),
            "notes": string_field("notes", max_length(5000), sample="Note"),
        },
        aliases={
            "phone": "phone_number",
            "phoneNumbers": "phone_numbers",
            "communicationWay": "communication_way",
            "companyId": "company",
            "leadCompanyName": "lead_company_name",
            "assignedTo": "assigned_to",
        },
    )


_load()
