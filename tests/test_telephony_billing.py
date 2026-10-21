"""
Subscription → phone system: end_expired_subscriptions suspends outbound calls of
tenants that lost the telephony entitlement and resumes the ones it suspended.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from subscriptions.models import Subscription
from telephony.models import PbxArtifact, PbxTenant
from telephony.services import billing
from tests.telephony_fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.django_db


@pytest.fixture
def live(tenant_a, telephony_subscription, worker):
    PbxTenant.objects.filter(pk=tenant_a.pk).update(status=PbxTenant.Status.ACTIVE, applied_version=1, config_version=1)
    tenant_a.refresh_from_db()
    return tenant_a


def _expire(company):
    Subscription.objects.filter(company=company).update(end_date=timezone.now() - timedelta(minutes=1))


def test_expired_subscription_suspends_outbound_and_blocks_the_node(live, company, worker):
    _expire(company)
    call_command("end_expired_subscriptions")
    live.refresh_from_db()
    assert live.outbound_suspended and live.suspended_for_billing
    assert PbxArtifact.objects.get(tenant=live, version=live.config_version).manifest["outbound"] == "suspended"
    assert [b["status"] for p, _, b in worker.requests if p == "/v1/tenants/outbound"] == ["blocked"]


def test_renewal_resumes_only_what_billing_suspended(live, company, worker):
    _expire(company)
    call_command("end_expired_subscriptions")
    Subscription.objects.filter(company=company).update(is_active=True, end_date=timezone.now() + timedelta(days=30))
    call_command("end_expired_subscriptions")
    live.refresh_from_db()
    assert not live.outbound_suspended and not live.suspended_for_billing
    assert [b["status"] for p, _, b in worker.requests if p == "/v1/tenants/outbound"] == ["blocked", "enabled"]


def test_a_fraud_suspension_is_not_lifted_by_billing(live, company):
    from telephony.services import provisioning

    PbxTenant.objects.filter(pk=live.pk).update(suspended_reason="Fraud alert: Outbound calls per hour")
    provisioning.request_job(live, provisioning.Job.Kind.SUSPEND)
    assert billing.reconcile() == {"suspended": 0, "resumed": 0, "resume_failed": 0}
    live.refresh_from_db()
    assert live.outbound_suspended


def test_plan_without_telephony_suspends(live, telephony_plan):
    telephony_plan.features = {"telephony": False}
    telephony_plan.save(update_fields=["features"])
    assert billing.reconcile()["suspended"] == 1


def test_node_that_cannot_unblock_keeps_the_suspension(live, company, worker):
    _expire(company)
    billing.reconcile()
    Subscription.objects.filter(company=company).update(is_active=True, end_date=timezone.now() + timedelta(days=30))
    worker.replies["/v1/tenants/outbound"] = (503, {"code": "ari_unavailable"})
    assert billing.reconcile() == {"suspended": 0, "resumed": 0, "resume_failed": 1}
    live.refresh_from_db()
    assert live.outbound_suspended and live.suspended_for_billing  # retried on the next run
