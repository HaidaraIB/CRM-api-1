import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from accounts.demo_accounts import get_demo_2fa_code_for_user, get_demo_account_kind


@pytest.mark.django_db
@override_settings(
    DEMO_META_ACCOUNT_USERNAME="meta_reviewer",
    DEMO_META_ACCOUNT_EMAIL="meta-reviewer@example.com",
    DEMO_META_ACCOUNT_2FA_CODE="246810",
)
def test_meta_demo_account_detection_and_2fa():
    User = get_user_model()
    user = User.objects.create_user(
        username="meta_reviewer",
        email="meta-reviewer@example.com",
        password="testpass123",
    )
    assert get_demo_account_kind(user) == "meta"
    assert get_demo_2fa_code_for_user(user) == "246810"


@pytest.mark.django_db
def test_non_demo_account_returns_none():
    User = get_user_model()
    user = User.objects.create_user(
        username="regular_user",
        email="user@example.com",
        password="testpass123",
    )
    assert get_demo_account_kind(user) is None
    assert get_demo_2fa_code_for_user(user) is None
