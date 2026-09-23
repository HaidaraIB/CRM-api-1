"""Super-admin email notifications when a company self-registers."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status

from accounts.event_emails import (
    _company_registration_payment_status,
    send_company_registration_admin_notifications,
)
from accounts.models import User
from companies.models import Company
from settings.models import SMTPSettings
from subscriptions.models import Plan, Subscription


@pytest.fixture
def smtp_settings_active(db):
    s = SMTPSettings.get_settings()
    s.is_active = True
    s.from_email = "noreply@example.com"
    s.from_name = "CRM"
    s.host = "unused"
    s.port = 587
    s.username = "unused"
    s.password = "unused"
    s.use_tls = True
    s.use_ssl = False
    s.save()
    return s


def _make_owner_company(name="Acme", domain="acme.example.com"):
    owner = User.objects.create_user(
        username=f"owner_{domain.split('.')[0]}",
        email=f"owner@{domain}",
        password="x",
        company=None,
        role="admin",
        first_name="Jane",
        last_name="Doe",
        phone="+9647701234567",
    )
    company = Company.objects.create(
        name=name,
        domain=domain,
        specialization="services",
        owner=owner,
    )
    owner.company = company
    owner.save(update_fields=["company"])
    return company, owner


@pytest.mark.django_db
def test_payment_status_requires_payment():
    assert _company_registration_payment_status(None, True, "en") == "Payment required"
    assert _company_registration_payment_status(None, True, "ar") == "يلزم إتمام الدفع"


@pytest.mark.django_db
def test_payment_status_no_plan():
    assert (
        _company_registration_payment_status(None, False, "en")
        == "No plan selected"
    )


@pytest.mark.django_db
def test_super_admin_notification_skips_non_super_admin_role(smtp_settings_active):
    company, owner = _make_owner_company("Skip Co", "skip.example.com")

    User.objects.create_user(
        username="django_super_only",
        email="django-super@platform.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
    )
    User.objects.create_user(
        username="real_super_admin",
        email="real-super@platform.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
    )

    with patch("accounts.event_emails._send_event_email", return_value=True) as mock_send:
        sent = send_company_registration_admin_notifications(company, owner)

    assert sent == 1
    mock_send.assert_called_once()
    args, _ = mock_send.call_args
    assert args[0].email == "real-super@platform.com"


@pytest.mark.django_db
def test_super_admin_notification_sends_to_multiple(smtp_settings_active):
    company, owner = _make_owner_company("Beta", "beta.example.com")

    User.objects.create_user(
        username="sa1",
        email="sa1@platform.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
        language="en",
    )
    User.objects.create_user(
        username="sa2",
        email="sa2@platform.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
        language="ar",
    )

    with patch("accounts.event_emails._send_event_email", return_value=True) as mock_send:
        sent = send_company_registration_admin_notifications(company, owner)

    assert sent == 2
    assert mock_send.call_count == 2
    args, _ = mock_send.call_args_list[0]
    _admin, _subject, template, context, _lang = args
    assert template == "company_registration_new_admin"
    assert context["company_name"] == "Beta"
    assert context["owner_email"] == "owner@beta.example.com"
    assert context["payment_status"] == "No plan selected"


@pytest.mark.django_db
def test_super_admin_notification_skips_excluded_email(smtp_settings_active):
    company, owner = _make_owner_company("Gamma", "gamma.example.com")

    User.objects.create_user(
        username="excluded_sa",
        email="admin@gmail.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
    )
    User.objects.create_user(
        username="included_sa",
        email="keeps@platform.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
    )

    with patch("accounts.event_emails._send_event_email", return_value=True) as mock_send:
        sent = send_company_registration_admin_notifications(company, owner)

    assert sent == 1
    mock_send.assert_called_once()
    args, _ = mock_send.call_args
    assert args[0].email == "keeps@platform.com"


@pytest.mark.django_db
def test_super_admin_notification_inactive_smtp_returns_zero(db):
    s = SMTPSettings.get_settings()
    s.is_active = False
    s.save()

    company, owner = _make_owner_company("Delta", "delta.example.com")
    User.objects.create_user(
        username="sa_only",
        email="only@p.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
    )

    with patch("accounts.event_emails._send_event_email") as mock_send:
        n = send_company_registration_admin_notifications(company, owner)

    assert n == 0
    mock_send.assert_not_called()


@pytest.mark.django_db
def test_super_admin_notification_includes_plan_and_trial_status(
    smtp_settings_active, db
):
    company, owner = _make_owner_company("Trial Co", "trial.example.com")
    plan = Plan.objects.create(
        name="Trial Plan",
        name_ar="خطة تجريبية",
        price_monthly=0,
        price_yearly=0,
        trial_days=30,
    )
    subscription = Subscription.objects.create(
        company=company,
        plan=plan,
        end_date=company.created_at,
        is_active=True,
    )

    User.objects.create_user(
        username="sa_trial",
        email="trial-admin@platform.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
        language="en",
    )

    with patch("accounts.event_emails._send_event_email", return_value=True) as mock_send:
        sent = send_company_registration_admin_notifications(
            company, owner, subscription=subscription, requires_payment=False
        )

    assert sent == 1
    args, _ = mock_send.call_args
    context = args[3]
    assert context["plan_name"] == "Trial Plan"
    assert context["payment_status"] == "Free trial (30 days)"


@pytest.mark.django_db
def test_register_company_triggers_admin_notification_async(api_client):
    User.objects.create_user(
        username="sa_reg",
        email="sa_reg@platform.com",
        password="x",
        company=None,
        role="super_admin",
        is_superuser=True,
    )
    payload = {
        "company": {
            "name": "New Reg Co",
            "domain": "new-reg-co",
            "specialization": "services",
        },
        "owner": {
            "first_name": "New",
            "last_name": "Owner",
            "email": "newowner@new-reg-co.com",
            "username": "new_reg_owner",
            "password": "StrongPass123!",
            "phone": "+9647709998888",
        },
    }

    with patch(
        "accounts.email_registration_policy.effective_registration_email_verification_required",
        return_value=False,
    ):
        with patch(
            "accounts.phone_otp_policy.effective_phone_otp_required",
            return_value=False,
        ):
            with patch(
                "accounts.views.registration._send_company_registration_admin_emails_async"
            ) as mock_async:
                r = api_client.post(
                    reverse("register_company"), payload, format="json"
                )

    assert r.status_code == status.HTTP_201_CREATED
    mock_async.assert_called_once()
    company_id, owner_id, subscription_id, requires_payment = mock_async.call_args[0]
    assert Company.objects.filter(pk=company_id, name="New Reg Co").exists()
    assert User.objects.filter(pk=owner_id, username="new_reg_owner").exists()
    assert subscription_id is None
    assert requires_payment is False
