from validation.registry import register_form
from validation.schema_dsl import (
    confirm_password_field,
    email_field,
    max_length,
    name_field,
    object_field,
    password_field,
    password_policy_params,
    phone_field,
    required,
    rule,
    string_field,
)

_SPECIALIZATIONS = ["real_estate", "services", "products", "medical"]


def _load():
    register_form(
        "auth.login",
        {
            "username": string_field("username", required(), max_length(254), sample="admin@example.com"),
            "password": string_field("password", required(), sample="Validpass1"),
        },
        public=True,
    )
    register_form(
        "auth.register",
        {
            "company": object_field(
                "company",
                {
                    "name": name_field(max_=64),
                    # `domain` stores a full hostname (e.g. "acme.example.com"), not a
                    # URL-safe slug segment — no `slug` rule here, matching
                    # tenant.upsert/tenant.create's `domain` field in schemas/tenants.py.
                    "domain": string_field(
                        "companyDomain",
                        required(),
                        max_length(256),
                        sample="acme.example.com",
                    ),
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
                    "phone": phone_field(client_required=False, server=False),
                },
                required(),
            ),
        },
        public=True,
        aliases={"confirmPassword": "owner.password"},
    )
    register_form(
        "auth.forgot_password",
        {"email": email_field()},
        public=True,
    )
    register_form(
        "auth.reset_password",
        {
            "email": email_field(),
            "new_password": password_field("newPassword"),
            "confirm_password": confirm_password_field("new_password", "confirmPassword"),
        },
        public=True,
        aliases={"password": "new_password", "confirmPassword": "confirm_password"},
    )
    register_form(
        "auth.change_password",
        {
            "current_password": string_field("currentPassword", required(), sample="Validpass1"),
            "new_password": password_field("newPassword"),
            "confirm_password": confirm_password_field("new_password", "confirmPassword"),
        },
        aliases={"currentPassword": "current_password", "newPassword": "new_password", "confirmPassword": "confirm_password"},
    )
    register_form(
        "auth.two_factor",
        {
            "code": string_field(
                "verificationCode",
                required(),
                rule("pattern", {"regex": r"\d{4,8}"}),
                sample="123456",
            )
        },
        public=True,
    )
    register_form(
        "auth.email_code",
        {
            "code": string_field(
                "verificationCode",
                required(),
                rule("pattern", {"regex": r"\d{4,8}"}),
                sample="123456",
            )
        },
        public=True,
    )
    register_form(
        "auth.phone_otp",
        {
            "phone": phone_field(client_required=False),
            "code": string_field(
                "verificationCode",
                required(),
                rule("pattern", {"regex": r"\d{4,8}"}),
                sample="123456",
            ),
        },
        public=True,
    )
    register_form(
        "user.upsert",
        {
            "first_name": name_field(label_key="firstName", client_required=True),
            "last_name": string_field("lastName", max_length(150), sample="User"),
            "email": email_field(client_required=True),
            "username": string_field(
                "username",
                required(client_only=True),
                rule("username", client_only=True),
                max_length(150),
            ),
            # User.phone is `blank=True, null=True` (accounts/models.py) and, unlike
            # most "phone" fields, is stored digits-only without a leading "+" here
            # (UserSerializer does no E.164 normalization) — no phone_e164 rule.
            "phone": string_field("phone", required(client_only=True), sample="1234567890"),
            "password": string_field(
                "password",
                rule("password_policy", password_policy_params(), client_only=True),
                sample="Validpass1",
            ),
            "role": string_field(
                "role",
                required(client_only=True),
                rule("one_of", {"values": ["super_admin", "admin", "company_admin", "supervisor", "employee", "data_entry", "reception", "doctor", "call_center"]}, client_only=True),
                sample="employee",
            ),
        },
        aliases={"firstName": "first_name", "lastName": "last_name"},
    )
    register_form(
        "limited_admin.create",
        {
            "username": string_field("username", required(), rule("username", client_only=True)),
            "email": email_field(),
            "password": password_field(),
            "first_name": name_field(label_key="firstName"),
            "last_name": name_field(label_key="lastName"),
        },
        aliases={"firstName": "first_name", "lastName": "last_name"},
    )
    register_form(
        "supervisor.create",
        {
            "username": string_field("username", required(), rule("username", client_only=True)),
            "email": email_field(),
            "password": password_field(),
            "first_name": name_field(label_key="firstName"),
            "last_name": name_field(label_key="lastName", client_required=True),
            "phone": phone_field(client_required=False),
        },
        aliases={"firstName": "first_name", "lastName": "last_name"},
    )
    register_form(
        "profile.update",
        {
            "first_name": name_field(label_key="firstName", client_required=True),
            "last_name": name_field(label_key="lastName", client_required=True),
            "email": email_field(client_required=True),
            "phone": phone_field(),
        },
        aliases={"firstName": "first_name", "lastName": "last_name"},
    )
    register_form(
        "admin_user.upsert",
        {
            "name": name_field(client_required=True),
            "email": email_field(client_required=True),
        },
    )


_load()
