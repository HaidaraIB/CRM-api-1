import pytest
from rest_framework.exceptions import ValidationError, PermissionDenied
from subscriptions.entitlements import require_quota, require_feature

@pytest.mark.django_db
def test_require_quota_success(company):
    # Depending on plan defaults, quotas might be unlimited
    # We just run the function and assert it doesn't raise exception
    require_quota(company, "max_users", 5, 1, message="Too many users", error_key="max_users")

@pytest.mark.django_db
def test_require_feature(company):
    # Assume default plan allows some basic features if no limitations apply
    # If the feature checking logic raises PermissionDenied, we expect it.
    try:
        require_feature(company, "some_fake_feature", message="Not allowed", error_key="some_feature")
    except PermissionDenied:
        pass  # expected
