import pytest
from rest_framework import status

from accounts.models import Role, SupervisorPermission, User
from accounts.supervisor_permissions import ensure_supervisor_permissions_for_company
from conftest import api_body


@pytest.mark.django_db
def test_ensure_supervisor_permissions_creates_missing_profile(company):
    orphan = User.objects.create_user(
        username="orphan_supervisor",
        email="orphan_supervisor@test.com",
        password="testpass123",
        company=company,
        role=Role.SUPERVISOR.value,
    )
    assert not SupervisorPermission.objects.filter(user=orphan).exists()

    created = ensure_supervisor_permissions_for_company(company)

    assert created == 1
    sp = SupervisorPermission.objects.get(user=orphan)
    assert sp.is_active is True


@pytest.mark.django_db
def test_supervisor_list_backfills_orphan(authenticated_admin, company):
    orphan = User.objects.create_user(
        username="orphan_supervisor_list",
        email="orphan_supervisor_list@test.com",
        password="testpass123",
        company=company,
        role=Role.SUPERVISOR.value,
    )
    assert not SupervisorPermission.objects.filter(user=orphan).exists()

    response = authenticated_admin.get("/api/v1/supervisors/")
    assert response.status_code == status.HTTP_200_OK

    data = api_body(response)
    results = data.get("results", data)
    user_ids = {item["user"]["id"] for item in results}
    assert orphan.id in user_ids
    assert SupervisorPermission.objects.filter(user=orphan).exists()
