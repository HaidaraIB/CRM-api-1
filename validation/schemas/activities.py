from validation.registry import register_form
from validation.schema_dsl import date_field, integer_field, max_length, name_field, required, rule, string_field


def _load():
    # Task/ClientTask/ClientCall/ClientVisit/ClientFieldVisit (crm/models.py) make
    # stage/call_method/visit_type/notes/dates all `blank=True, null=True` — only
    # `Task.deal` is a non-nullable FK. Real creation paths (quick log, inbound,
    # imports) legitimately omit the rest, so those keep `required(client_only=True)`
    # (clients still nudge for them) but the server never enforces their presence.
    register_form(
        "task.upsert",
        {
            "deal": integer_field("deal", required(), sample=1),
            "notes": string_field("notes", required(client_only=True), max_length(5000), sample="Follow up"),
            "reminder_date": date_field("reminderDate"),
        },
        aliases={"reminderDate": "reminder_date"},
    )
    register_form(
        "campaign.upsert",
        {
            "name": name_field(),
            "description": string_field("description", max_length(5000), sample="Details"),
        },
    )
    register_form(
        "lead_action.create",
        {
            "stage": integer_field("actionType", required(client_only=True), ui_key="stageId", sample=1),
            "notes": string_field("notes", required(client_only=True), max_length(5000), sample="Called the lead"),
        },
        aliases={"stageId": "stage"},
    )
    register_form(
        "lead_call.create",
        {
            "call_method": integer_field("callMethod", required(client_only=True), ui_key="callMethod", sample=1),
            "notes": string_field("notes", max_length(5000), sample="Spoke with the lead"),
            "result": string_field("result", max_length(255), sample="Interested"),
        },
        aliases={"callMethod": "call_method"},
    )
    register_form(
        "lead_visit.create",
        {
            "visit_type": integer_field("visitType", required(client_only=True), ui_key="visitType", sample=1),
            "notes": string_field("notes", max_length(5000), sample="Site visit"),
            "visit_date": date_field("visitDate", required(client_only=True)),
        },
        aliases={"visitType": "visit_type", "visitDate": "visit_date"},
    )
    register_form(
        "field_visit.create",
        {
            "visit_type": integer_field("visitType", ui_key="visitType", sample=1),
            "notes": string_field("notes", required(client_only=True), max_length(5000), sample="On site"),
            "scheduled_at": date_field("scheduledAt", required(client_only=True)),
        },
        aliases={"visitType": "visit_type", "scheduledAt": "scheduled_at"},
    )


_load()
