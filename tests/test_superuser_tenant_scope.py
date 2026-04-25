import pytest
from rest_framework.test import APIRequestFactory

from accounts.models import Role, User
from accounts.permissions import CanViewDashboard
from accounts.views.tokens_users import UserViewSet
from companies.models import Company


@pytest.mark.django_db
def test_superuser_admin_user_queryset_is_company_scoped():
    owner_a = User.objects.create_user(
        username="owner_a",
        email="owner_a@example.com",
        password="pass12345",
        role=Role.ADMIN.value,
    )
    company_a = Company.objects.create(
        name="Company A",
        domain="company-a.example.com",
        specialization="services",
        owner=owner_a,
    )
    owner_a.company = company_a
    owner_a.is_superuser = True
    owner_a.save(update_fields=["company", "is_superuser"])

    user_a = User.objects.create_user(
        username="user_a",
        email="user_a@example.com",
        password="pass12345",
        role=Role.EMPLOYEE.value,
        company=company_a,
    )

    owner_b = User.objects.create_user(
        username="owner_b",
        email="owner_b@example.com",
        password="pass12345",
        role=Role.ADMIN.value,
    )
    company_b = Company.objects.create(
        name="Company B",
        domain="company-b.example.com",
        specialization="products",
        owner=owner_b,
    )
    owner_b.company = company_b
    owner_b.save(update_fields=["company"])

    user_b = User.objects.create_user(
        username="user_b",
        email="user_b@example.com",
        password="pass12345",
        role=Role.EMPLOYEE.value,
        company=company_b,
    )

    factory = APIRequestFactory()
    request = factory.get("/api/users/")
    request.user = owner_a

    view = UserViewSet()
    view.request = request
    qs_ids = set(view.get_queryset().values_list("id", flat=True))

    assert owner_a.id in qs_ids
    assert user_a.id in qs_ids
    assert owner_b.id not in qs_ids
    assert user_b.id not in qs_ids


@pytest.mark.django_db
def test_superuser_admin_cannot_access_other_company_data():
    owner_a = User.objects.create_user(
        username="access_owner_a",
        email="access_owner_a@example.com",
        password="pass12345",
        role=Role.ADMIN.value,
    )
    company_a = Company.objects.create(
        name="Access Company A",
        domain="access-company-a.example.com",
        specialization="real_estate",
        owner=owner_a,
    )
    owner_a.company = company_a
    owner_a.is_superuser = True
    owner_a.save(update_fields=["company", "is_superuser"])

    owner_b = User.objects.create_user(
        username="access_owner_b",
        email="access_owner_b@example.com",
        password="pass12345",
        role=Role.ADMIN.value,
    )
    company_b = Company.objects.create(
        name="Access Company B",
        domain="access-company-b.example.com",
        specialization="services",
        owner=owner_b,
    )
    owner_b.company = company_b
    owner_b.save(update_fields=["company"])

    assert owner_a.can_access_tenant_company_data(company_a) is True
    assert owner_a.can_access_tenant_company_data(company_b) is False
    assert owner_a.can_access_company_data(company_b) is False


@pytest.mark.django_db
def test_super_admin_panel_permission_still_allows_superuser():
    user = User(username="panel_super", is_superuser=True, role=Role.ADMIN.value)
    request = APIRequestFactory().get("/")
    request.user = user

    assert CanViewDashboard().has_permission(request, view=None) is True
