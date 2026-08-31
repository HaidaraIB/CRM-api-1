"""
Regression tests for _latest_activity_maps().

This helper used to stream every scoped task/call/visit row into Python to find
the newest one per client. It now asks the database for exactly those rows, so
these tests pin the selection behaviour that rewrite has to preserve: newest
wins, per client, across all three activity kinds.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from crm.dashboard_summary import _latest_activity_maps
from crm.models import Client, ClientCall, ClientTask, ClientVisit


@pytest.mark.django_db
class TestLatestActivityMaps:
    def _client(self, company, name):
        return Client.objects.create(
            name=name, company=company, priority="low", type="cold"
        )

    def test_returns_newest_row_per_client(self, admin_user, company):
        lead = self._client(company, "Lead A")
        now = timezone.now()

        old = ClientTask.objects.create(
            client=lead, reminder_date=now, notes="old task"
        )
        newest = ClientTask.objects.create(
            client=lead, reminder_date=now, notes="new task"
        )
        # created_at is auto_now_add, so set it explicitly to control ordering.
        ClientTask.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=3))
        ClientTask.objects.filter(pk=newest.pk).update(created_at=now - timedelta(hours=1))

        result = _latest_activity_maps(admin_user, [lead.id])
        kind, row = result[lead.id]
        assert kind == "task"
        assert row.id == newest.id

    def test_newest_wins_across_activity_kinds(self, admin_user, company):
        lead = self._client(company, "Lead B")
        now = timezone.now()

        task = ClientTask.objects.create(client=lead, reminder_date=now, notes="t")
        call = ClientCall.objects.create(client=lead, notes="c")
        visit = ClientVisit.objects.create(client=lead, summary="v")

        ClientTask.objects.filter(pk=task.pk).update(created_at=now - timedelta(days=2))
        ClientVisit.objects.filter(pk=visit.pk).update(created_at=now - timedelta(days=1))
        ClientCall.objects.filter(pk=call.pk).update(created_at=now - timedelta(minutes=5))

        kind, row = _latest_activity_maps(admin_user, [lead.id])[lead.id]
        assert kind == "call"
        assert row.id == call.id

    def test_each_client_resolved_independently(self, admin_user, company):
        first = self._client(company, "Lead C")
        second = self._client(company, "Lead D")
        now = timezone.now()

        first_task = ClientTask.objects.create(
            client=first, reminder_date=now, notes="first"
        )
        second_call = ClientCall.objects.create(client=second, notes="second")
        ClientTask.objects.filter(pk=first_task.pk).update(created_at=now - timedelta(days=5))
        ClientCall.objects.filter(pk=second_call.pk).update(created_at=now - timedelta(days=4))

        result = _latest_activity_maps(admin_user, [first.id, second.id])
        assert result[first.id][0] == "task"
        assert result[first.id][1].id == first_task.id
        assert result[second.id][0] == "call"
        assert result[second.id][1].id == second_call.id

    def test_clients_without_activity_are_absent(self, admin_user, company):
        lead = self._client(company, "Lead E")
        assert _latest_activity_maps(admin_user, [lead.id]) == {}

    def test_empty_input(self, admin_user):
        assert _latest_activity_maps(admin_user, []) == {}

    def test_cost_does_not_grow_with_history(
        self, admin_user, company, django_assert_max_num_queries
    ):
        """
        One query per activity kind regardless of how much history exists.

        The old implementation read every row, so this is the property that
        actually changed and the one worth guarding.
        """
        lead = self._client(company, "Lead F")
        now = timezone.now()
        for i in range(30):
            task = ClientTask.objects.create(
                client=lead, reminder_date=now, notes=f"task {i}"
            )
            ClientTask.objects.filter(pk=task.pk).update(
                created_at=now - timedelta(days=30 - i)
            )

        with django_assert_max_num_queries(3):
            kind, row = _latest_activity_maps(admin_user, [lead.id])[lead.id]

        assert kind == "task"
        assert row.notes == "task 29"
