from validation.registry import register_form
from validation.schema_dsl import email_field, max_length, name_field, number_field, phone_field, rule, string_field


def _named(form_id: str):
    register_form(
        form_id,
        {
            "name": name_field(),
            "description": string_field("description", max_length(5000), sample="Details"),
        },
    )


def _load():
    _named("product_category.upsert")
    register_form(
        "product.upsert",
        {
            "name": name_field(),
            "sku": string_field("sku", max_length(64), sample="SKU-1"),
            "price": number_field("price", rule("number_range", {"min": 0}, client_only=True), sample=10),
            "description": string_field("description", max_length(5000), sample="Details"),
        },
    )
    register_form(
        "supplier.upsert",
        {
            "name": name_field(),
            # Supplier.email/phone are `blank=True, null=True` (products/models.py) —
            # server never enforces presence, only format when present.
            "email": email_field(client_required=True),
            "phone": phone_field(),
            "description": string_field("description", max_length(5000), sample="Details"),
        },
    )
    _named("service.upsert")
    register_form(
        "service_package.upsert",
        {
            "name": name_field(),
            "price": number_field("price", rule("number_range", {"min": 0}, client_only=True), sample=10),
            "description": string_field("description", max_length(5000), sample="Details"),
        },
    )
    register_form(
        "service_provider.upsert",
        {
            "name": name_field(),
            "email": email_field(client_required=True),
            "phone": phone_field(),
        },
    )
    _named("developer.upsert")
    _named("project.upsert")
    register_form(
        "unit.upsert",
        {
            "name": name_field(),
            "price": number_field("price", rule("number_range", {"min": 0}, client_only=True), sample=1000),
            "description": string_field("description", max_length(5000), sample="Details"),
        },
    )
    register_form(
        "owner.upsert",
        {
            "name": name_field(),
            "email": string_field("email", rule("email"), max_length(254), sample="owner@example.com"),
            "phone": phone_field(),
        },
    )


_load()
