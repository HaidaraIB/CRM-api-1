"""
Walk-in arrival announcements (CALL_CENTER): routing matrix, cooldown, acknowledge,
and the escalation management command.
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from conftest import api_body


@pytest.fixture
def unassigned_client(company, db):
    from crm.models import Client

    return Client.objects.create(
        name="Walk-in Lead", company=company, priority="low", type="cold",
    )


@pytest.mark.django_db
class TestArrivalRoutingMatrix:
    def test_existing_on_shift_assignee_notified_no_reassign(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])

        response = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        body = api_body(response)
        assert body["routing"] == "existing_assignee"
        assert body["notified_user_names"] == [
            employee_user.get_full_name() or employee_user.username
        ]
        unassigned_client.refresh_from_db()
        assert unassigned_client.assigned_to_id == employee_user.id

    def test_existing_off_shift_assignee_notifies_owner_and_supervisor_not_employee(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        from accounts.models import SupervisorPermission, User

        employee_user.work_start_time = "09:00"
        employee_user.work_end_time = "10:00"
        # Guarantee "now" falls outside the window regardless of wall-clock time.
        import datetime as dt

        now_t = timezone.localtime().time()
        if dt.time(9, 0) <= now_t <= dt.time(10, 0):
            employee_user.work_start_time = "23:58"
            employee_user.work_end_time = "23:59"
        employee_user.save(update_fields=["work_start_time", "work_end_time"])

        supervisor = User.objects.create_user(
            username="mgr_leads_supervisor",
            email="mgrleads@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor, is_active=True, can_manage_leads=True,
        )

        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])

        response = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        body = api_body(response)
        assert body["routing"] == "owner_assignee_off_shift"
        names = set(body["notified_user_names"])
        assert (company.owner.get_full_name() or company.owner.username) in names
        assert (supervisor.get_full_name() or supervisor.username) in names
        assert (employee_user.get_full_name() or employee_user.username) not in names

        # Lead ownership must be untouched.
        unassigned_client.refresh_from_db()
        assert unassigned_client.assigned_to_id == employee_user.id

    def test_unassigned_lead_auto_assigns_to_eligible_employee(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        response = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        body = api_body(response)
        assert body["routing"] == "auto_assigned"
        unassigned_client.refresh_from_db()
        assert unassigned_client.assigned_to_id == employee_user.id

    def test_unassigned_lead_no_eligible_employee_falls_back_to_owner(
        self, authenticated_call_center, company, unassigned_client,
    ):
        # No active employees in this company at all.
        response = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        body = api_body(response)
        assert body["routing"] == "owner_no_eligible"
        assert body["notified_user_names"] == [
            company.owner.get_full_name() or company.owner.username
        ]
        unassigned_client.refresh_from_db()
        assert unassigned_client.assigned_to_id is None

    def test_writes_exactly_one_client_event_and_no_status_change(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        from crm.models import ClientEvent

        original_status_id = unassigned_client.status_id
        response = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        events = ClientEvent.objects.filter(
            client=unassigned_client, event_type="customer_arrived"
        )
        assert events.count() == 1

        unassigned_client.refresh_from_db()
        assert unassigned_client.status_id == original_status_id


@pytest.mark.django_db
class TestArrivalCooldown:
    def test_reannounce_within_cooldown_returns_409_with_existing_arrival(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        first = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert first.status_code == status.HTTP_201_CREATED
        first_id = api_body(first)["id"]

        second = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert second.status_code == status.HTTP_409_CONFLICT
        payload = second.json()
        assert payload["error"]["code"] == "arrival_cooldown_active"
        assert payload["error"]["details"]["arrival"]["id"] == first_id

    def test_reannounce_after_cooldown_creates_second_arrival(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        from crm.models import LeadArrival

        first = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert first.status_code == status.HTTP_201_CREATED

        stale_time = timezone.now() - timedelta(seconds=301)
        LeadArrival.objects.filter(client=unassigned_client).update(announced_at=stale_time)

        second = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert second.status_code == status.HTTP_201_CREATED
        assert LeadArrival.objects.filter(client=unassigned_client).count() == 2


@pytest.mark.django_db
class TestArrivalAcknowledge:
    def test_notified_user_can_acknowledge_idempotently(
        self, api_client, authenticated_call_center, company, employee_user, unassigned_client, subscription,
    ):
        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])
        announce = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]

        api_client.force_authenticate(user=employee_user)
        first_ack = api_client.post(f"/api/v1/lead-arrivals/{arrival_id}/acknowledge/")
        assert first_ack.status_code == status.HTTP_200_OK
        first_body = api_body(first_ack)
        assert first_body["status"] == "acknowledged"
        acknowledged_at = first_body["acknowledged_at"]

        second_ack = api_client.post(f"/api/v1/lead-arrivals/{arrival_id}/acknowledge/")
        assert second_ack.status_code == status.HTTP_200_OK
        assert api_body(second_ack)["acknowledged_at"] == acknowledged_at

    def test_unrelated_employee_cannot_acknowledge(
        self, api_client, authenticated_call_center, company, employee_user, unassigned_client, subscription,
    ):
        from accounts.models import User

        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])
        announce = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]

        other_employee = User.objects.create_user(
            username="other_employee_ack",
            email="otheremp@test.com",
            password="testpass123",
            company=company,
            role="employee",
        )
        # Outside their notified/assigned scope entirely -> hidden like any other
        # out-of-scope object in this API (see test_cross_tenant_lead_not_found),
        # not a 403 that would confirm the arrival's existence.
        api_client.force_authenticate(user=other_employee)
        response = api_client.post(f"/api/v1/lead-arrivals/{arrival_id}/acknowledge/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_owner_can_acknowledge(
        self, api_client, admin_user, call_center_user, company, employee_user, unassigned_client, subscription,
    ):
        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])

        api_client.force_authenticate(user=call_center_user)
        announce = api_client.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]

        api_client.force_authenticate(user=admin_user)
        response = api_client.post(f"/api/v1/lead-arrivals/{arrival_id}/acknowledge/")
        assert response.status_code == status.HTTP_200_OK

    def test_call_center_can_acknowledge_from_board_on_recipients_behalf(
        self, api_client, call_center_user, company, employee_user, unassigned_client, subscription,
    ):
        # The desk (front-of-house) can see the whole board and may confirm receipt
        # even though they are not themselves a notified recipient.
        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])

        api_client.force_authenticate(user=call_center_user)
        announce = api_client.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]

        response = api_client.post(f"/api/v1/lead-arrivals/{arrival_id}/acknowledge/")
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["status"] == "acknowledged"


@pytest.mark.django_db
class TestArrivalEscalationCommand:
    def test_escalates_past_due_and_is_idempotent(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        from django.core.management import call_command

        from accounts.models import SupervisorPermission, User
        from crm.models import LeadArrival
        from notifications.models import Notification, NotificationType

        supervisor = User.objects.create_user(
            username="escalation_supervisor",
            email="escsup@test.com",
            password="testpass123",
            company=company,
            role="supervisor",
        )
        SupervisorPermission.objects.create(
            user=supervisor, is_active=True, can_manage_leads=True,
        )

        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])
        announce = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]

        LeadArrival.objects.filter(pk=arrival_id).update(
            escalation_due_at=timezone.now() - timedelta(minutes=1)
        )

        call_command("check_lead_arrival_escalations")
        call_command("check_lead_arrival_escalations")  # must not double-send

        arrival = LeadArrival.objects.get(pk=arrival_id)
        assert arrival.escalated_at is not None

        escalated = Notification.objects.filter(
            type=NotificationType.CUSTOMER_ARRIVAL_ESCALATED,
            data__arrival_id=arrival_id,
        )
        recipient_ids = set(escalated.values_list("user_id", flat=True))
        assert recipient_ids == {company.owner_id, supervisor.id}
        assert escalated.count() == 2  # not duplicated on the second run

    def test_acknowledged_arrival_never_escalates(
        self, api_client, authenticated_call_center, company, employee_user, unassigned_client, subscription,
    ):
        from django.core.management import call_command

        from crm.models import LeadArrival

        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])
        announce = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]

        api_client.force_authenticate(user=employee_user)
        api_client.post(f"/api/v1/lead-arrivals/{arrival_id}/acknowledge/")

        LeadArrival.objects.filter(pk=arrival_id).update(
            escalation_due_at=timezone.now() - timedelta(minutes=1)
        )
        call_command("check_lead_arrival_escalations")

        arrival = LeadArrival.objects.get(pk=arrival_id)
        assert arrival.escalated_at is None

    def test_escalation_disabled_never_sets_due_at(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        company.arrival_escalation_enabled = False
        company.save(update_fields=["arrival_escalation_enabled"])

        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])
        response = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        assert api_body(response)["escalation_due_at"] is None

    def test_dry_run_writes_no_dispatch_log(
        self, authenticated_call_center, company, employee_user, unassigned_client,
    ):
        from django.core.management import call_command

        from crm.models import LeadArrival
        from notifications.models import ReminderDispatchLog

        unassigned_client.assigned_to = employee_user
        unassigned_client.save(update_fields=["assigned_to"])
        announce = authenticated_call_center.post(
            "/api/v1/lead-arrivals/", {"client": unassigned_client.id}, format="json",
        )
        arrival_id = api_body(announce)["id"]
        LeadArrival.objects.filter(pk=arrival_id).update(
            escalation_due_at=timezone.now() - timedelta(minutes=1)
        )

        call_command("check_lead_arrival_escalations", "--dry-run")

        assert not ReminderDispatchLog.objects.exists()
        assert LeadArrival.objects.get(pk=arrival_id).escalated_at is None
