"""Super-admin email notifications when a support ticket is created."""

from unittest.mock import patch

import pytest
from accounts.event_emails import (
    send_support_ticket_new_admin_notifications,
    _support_ticket_description_preview,
)
from accounts.models import User
from companies.models import Company
from support.models import SupportTicket
from settings.models import SMTPSettings


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


@pytest.mark.django_db
def test_description_preview_truncates():
    ticket = type("T", (), {"description": "x" * 500})()
    out = _support_ticket_description_preview(ticket, max_len=400)
    assert len(out) == 400
    assert out.endswith("…")


@pytest.mark.django_db
def test_super_admin_notification_skips_creator_when_superuser(smtp_settings_active):
    """Creator is superuser: they do not get the admin alert; another superuser still does."""
    creator_super = User.objects.create_user(
        username="creator_super",
        email="super_creator@platform.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
    )
    company = Company.objects.create(
        name="Acme",
        domain="acme.example.com",
        owner=creator_super,
    )
    creator_super.company = company
    creator_super.save(update_fields=["company"])

    User.objects.create_user(
        username="super_other",
        email="ops@platform.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
    )

    ticket = SupportTicket.objects.create(
        title="Help",
        description="Need assistance",
        company=company,
        created_by=creator_super,
    )

    with patch("accounts.event_emails._send_event_email") as mock_send:
        sent = send_support_ticket_new_admin_notifications(creator_super, ticket)

    assert sent == 1
    mock_send.assert_called_once()
    args, _kwargs = mock_send.call_args
    admin_user, _subject, template, context, _lang = args
    assert admin_user.email == "ops@platform.com"
    assert template == "support_ticket_new_admin"
    assert context["company_name"] == "Acme"
    assert "super_creator@platform.com" in context["creator_line"]


@pytest.mark.django_db
def test_super_admin_notification_sends_to_multiple(smtp_settings_active):
    owner = User.objects.create_user(
        username="owner2",
        email="owner2@tenant.com",
        password="x",
        company=None,
        role="admin",
    )
    company = Company.objects.create(
        name="Beta",
        domain="beta.example.com",
        owner=owner,
    )
    owner.company = company
    owner.save(update_fields=["company"])

    User.objects.create_user(
        username="sa1",
        email="sa1@platform.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
        language="en",
    )
    User.objects.create_user(
        username="sa2",
        email="sa2@platform.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
        language="ar",
    )

    ticket = SupportTicket.objects.create(
        title="Bug",
        description="x",
        company=company,
        created_by=owner,
    )

    with patch("accounts.event_emails._send_event_email", return_value=True) as mock_send:
        sent = send_support_ticket_new_admin_notifications(owner, ticket)

    assert sent == 2
    assert mock_send.call_count == 2


@pytest.mark.django_db
def test_super_admin_notification_skips_excluded_email(smtp_settings_active):
    owner = User.objects.create_user(
        username="owner_ex",
        email="owner_ex@tenant.com",
        password="x",
        company=None,
        role="admin",
    )
    company = Company.objects.create(
        name="Gamma",
        domain="gamma.example.com",
        owner=owner,
    )
    owner.company = company
    owner.save(update_fields=["company"])

    User.objects.create_user(
        username="excluded_sa",
        email="admin@gmail.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
    )
    User.objects.create_user(
        username="included_sa",
        email="keeps@platform.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
    )

    ticket = SupportTicket.objects.create(
        title="Q",
        description="d",
        company=company,
        created_by=owner,
    )

    with patch("accounts.event_emails._send_event_email", return_value=True) as mock_send:
        sent = send_support_ticket_new_admin_notifications(owner, ticket)

    assert sent == 1
    mock_send.assert_called_once()
    args, _ = mock_send.call_args
    assert args[0].email == "keeps@platform.com"


@pytest.mark.django_db
def test_super_admin_notification_inactive_smtp_returns_zero(db):
    from settings.models import SMTPSettings

    s = SMTPSettings.get_settings()
    s.is_active = False
    s.save()

    owner = User.objects.create_user(
        username="o3",
        email="o3@t.com",
        password="x",
        company=None,
        role="admin",
    )
    company = Company.objects.create(
        name="C",
        domain="c.example.com",
        owner=owner,
    )
    User.objects.create_user(
        username="sa_only",
        email="only@p.com",
        password="x",
        company=None,
        role="admin",
        is_superuser=True,
    )
    ticket = SupportTicket.objects.create(
        title="T",
        description="d",
        company=company,
        created_by=owner,
    )

    with patch("accounts.event_emails._send_event_email") as mock_send:
        n = send_support_ticket_new_admin_notifications(owner, ticket)

    assert n == 0
    mock_send.assert_not_called()
