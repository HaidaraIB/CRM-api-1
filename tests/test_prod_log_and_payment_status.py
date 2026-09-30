"""Payment-status poll re-query rules, gateway logging, and important-log 401 filter."""

import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.utils import timezone

from crm_saas_api.logging_filters import ImportantOnlyFilter
from subscriptions.models import PaymentStatus
from subscriptions.services.payment_completion import (
    payment_eligible_for_status_poll_requery,
    query_gateway_state,
)
from tests.test_gateway_return_handlers import _pending_payment, _gateway


@pytest.mark.django_db
def test_payment_eligible_for_requery_only_fresh_pending(subscription, plan):
    gw = _gateway("stripe")
    amount = str(plan.price_monthly)
    payment = _pending_payment(subscription, gw, plan, "cs_test", amount=amount)
    payment.session_expires_at = timezone.now() + timedelta(minutes=20)
    payment.save(update_fields=["session_expires_at", "updated_at"])
    assert payment_eligible_for_status_poll_requery(payment) is True

    payment.payment_status = PaymentStatus.COMPLETED.value
    payment.save(update_fields=["payment_status", "updated_at"])
    assert payment_eligible_for_status_poll_requery(payment) is False

    payment.payment_status = PaymentStatus.PENDING.value
    payment.session_expires_at = timezone.now() - timedelta(hours=30)
    payment.save(update_fields=["payment_status", "session_expires_at", "updated_at"])
    assert payment_eligible_for_status_poll_requery(payment) is False


@pytest.mark.django_db
def test_check_payment_status_skips_gateway_for_completed_payment(
    api_client, subscription, plan, owner_user
):
    gw = _gateway("stripe")
    amount = str(plan.price_monthly)
    payment = _pending_payment(subscription, gw, plan, "cs_completed_skip", amount=amount)
    payment.payment_status = PaymentStatus.COMPLETED.value
    payment.save(update_fields=["payment_status", "updated_at"])
    api_client.force_authenticate(user=owner_user)

    with patch(
        "subscriptions.views.check_payment.query_gateway_state"
    ) as mock_query:
        res = api_client.get(f"/api/payment-status/{subscription.id}/")

    assert res.status_code == 200
    mock_query.assert_not_called()


@pytest.mark.django_db
def test_check_payment_status_skips_gateway_for_stale_pending(
    api_client, subscription, plan, owner_user
):
    gw = _gateway("qicard")
    amount = str(plan.price_monthly)
    payment = _pending_payment(subscription, gw, plan, "qi_stale", amount=amount)
    payment.session_expires_at = timezone.now() - timedelta(hours=48)
    payment.save(update_fields=["session_expires_at", "updated_at"])
    api_client.force_authenticate(user=owner_user)

    with patch(
        "subscriptions.views.check_payment.query_gateway_state"
    ) as mock_query:
        res = api_client.get(f"/api/payment-status/{subscription.id}/")

    assert res.status_code == 200
    mock_query.assert_not_called()
    body = res.json()["data"]
    assert body["gateway_status"] == "pending"


@pytest.mark.django_db
def test_query_gateway_state_logs_warning_on_transport_error(subscription, plan):
    gw = _gateway("qicard")
    payment = _pending_payment(subscription, gw, plan, "tran-transport", amount=str(plan.price_monthly))
    adapter = MagicMock()
    adapter.slug = "zaincash"
    adapter.verify.side_effect = requests.ConnectionError("522 origin down")

    with patch(
        "subscriptions.services.payment_completion.adapter_for_payment",
        return_value=adapter,
    ):
        with patch(
            "subscriptions.services.payment_completion.logger"
        ) as mock_logger:
            result = query_gateway_state(payment)

    assert result.state == "unknown"
    mock_logger.warning.assert_called()
    mock_logger.exception.assert_not_called()


def test_important_only_filter_drops_non_auth_401():
    filt = ImportantOnlyFilter()
    record = logging.LogRecord(
        name="django.request",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Unauthorized: /api/v1/sync/digest/",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is False


def test_important_only_filter_keeps_auth_401():
    filt = ImportantOnlyFilter()
    record = logging.LogRecord(
        name="django.request",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Unauthorized: /api/v1/auth/login/",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
