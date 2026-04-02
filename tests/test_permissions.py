import pytest
from rest_framework.test import APIRequestFactory
from accounts.permissions import CanViewDashboard, CanManageTenants

from accounts.models import LimitedAdmin
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.fixture
def super_admin_user():
    return User(username="super", is_superuser=True)

@pytest.fixture
def limited_admin_user(db):
    user = User.objects.create_user(username="la_user", password="123")
    la = LimitedAdmin.objects.create(user=user, is_active=True)
    return user

@pytest.fixture
def factory():
    return APIRequestFactory()

@pytest.fixture
def view():
    class MockView:
        pass
    return MockView()

@pytest.mark.django_db
def test_superadmin_access(super_admin_user, factory, view):
    request = factory.get('/')
    request.user = super_admin_user
    perm = CanViewDashboard()
    assert perm.has_permission(request, view) is True

@pytest.mark.django_db
def test_limited_admin_access_dashboard(limited_admin_user, factory, view):
    request = factory.get('/')
    request.user = limited_admin_user
    la = request.user.limited_admin_profile
    la.can_view_dashboard = True
    la.save()
    perm = CanViewDashboard()
    assert perm.has_permission(request, view) is True

@pytest.mark.django_db
def test_limited_admin_manage_tenants_read(limited_admin_user, factory, view):
    request = factory.get('/')
    request.user = limited_admin_user
    la = request.user.limited_admin_profile
    la.can_view_dashboard = True
    la.can_manage_tenants = False
    la.save()
    perm = CanManageTenants()
    # Read access allowed via can_view_dashboard
    assert perm.has_permission(request, view) is True

@pytest.mark.django_db
def test_limited_admin_manage_tenants_write(limited_admin_user, factory, view):
    request = factory.post('/')
    request.user = limited_admin_user
    la = request.user.limited_admin_profile
    la.can_view_dashboard = True
    la.can_manage_tenants = False
    la.save()
    perm = CanManageTenants()
    # Write access denied (needs can_manage_tenants)
    assert perm.has_permission(request, view) is False
    
    la.can_manage_tenants = True
    la.save()
    assert perm.has_permission(request, view) is True
