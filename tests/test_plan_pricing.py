"""
Plan sale prices: resolution rules, checkout charging, the price lock that lets
a webhook land after a sale ended, and the admin/public plan API shape.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from conftest import api_body
from subscriptions.invoicing.snapshot import ensure_invoice_for_payment
from subscriptions.models import (
    BillingCycle,
    Payment,
    PaymentGateway,
    PaymentGatewayStatus,
    PaymentStatus,
    Plan,
    Subscription,
)
from subscriptions.services.billing import (
    apply_successful_payment,
    finalize_completed_payment,
    preview_plan_change,
    resolve_checkout_pricing,
    resolve_checkout_quote,
)
from subscriptions.services.pricing import cycle_price, sale_window_status


def _plan(**kw):
    defaults = dict(
        name="Pro",
        description="pro",
        price_monthly=Decimal("12.19"),
        price_yearly=Decimal("120.00"),
        tier=1,
    )
    defaults.update(kw)
    return Plan.objects.create(**defaults)


def _gateway(name="Stripe"):
    return PaymentGateway.objects.create(
        name=name, status=PaymentGatewayStatus.ACTIVE.value, enabled=True
    )


@pytest.mark.django_db
class TestCyclePrice:
    def test_no_sale_is_regular(self):
        p = _plan()
        cp = cycle_price(p, BillingCycle.MONTHLY)
        assert (cp.price, cp.regular, cp.on_sale, cp.discount_percent) == (
            Decimal("12.19"), Decimal("12.19"), False, 0
        )
        assert sale_window_status(p) == "none"

    def test_live_sale_without_window(self):
        p = _plan(sale_price_monthly=Decimal("2.49"))
        cp = cycle_price(p, BillingCycle.MONTHLY)
        assert cp.on_sale and cp.price == Decimal("2.49")
        # (12.19 - 2.49) / 12.19 = 79.57% -> floored, never overstated.
        assert cp.discount_percent == 79

    def test_cycles_are_independent(self):
        p = _plan(sale_price_yearly=Decimal("60.00"))
        assert not cycle_price(p, BillingCycle.MONTHLY).on_sale
        assert cycle_price(p, BillingCycle.YEARLY).price == Decimal("60.00")

    @pytest.mark.parametrize(
        "starts,ends,expected",
        [
            (+1, None, "scheduled"),
            (-2, -1, "ended"),
            (-1, +1, "live"),
            (None, +1, "live"),
        ],
    )
    def test_window(self, starts, ends, expected):
        now = timezone.now()
        p = _plan(
            sale_price_monthly=Decimal("5.00"),
            sale_starts_at=now + timedelta(days=starts) if starts is not None else None,
            sale_ends_at=now + timedelta(days=ends) if ends is not None else None,
        )
        assert sale_window_status(p, now) == expected
        assert cycle_price(p, BillingCycle.MONTHLY, at=now).on_sale is (expected == "live")

    def test_sale_not_below_regular_is_ignored(self):
        p = _plan(sale_price_monthly=Decimal("12.19"))
        assert not cycle_price(p, BillingCycle.MONTHLY).on_sale


def _sub(company, plan, *, active, days_left=30):
    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=active,
        start_date=now - timedelta(days=30 - days_left),
        end_date=now + timedelta(days=days_left),
        current_period_start=now - timedelta(days=30 - days_left),
        billing_cycle=BillingCycle.MONTHLY,
    )


def _completed(sub, gw, amount):
    return Payment.objects.create(
        subscription=sub,
        amount=amount,
        currency="USD",
        amount_usd=amount,
        payment_method=gw,
        payment_status=PaymentStatus.COMPLETED.value,
        tran_ref=f"done_{sub.id}_{amount}",
    )


@pytest.mark.django_db
class TestCheckoutQuote:
    def test_initial_charges_sale_price(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"))
        sub = _sub(company, p, active=False)
        q = resolve_checkout_quote(sub, target_plan_id=None, billing_cycle_param="monthly")
        assert q.intent == "initial"
        assert q.amount_usd == Decimal("2.49")
        assert q.regular_usd == Decimal("12.19")
        assert q.discount_usd == Decimal("9.70")
        snap = q.snapshot()
        assert snap["charged_usd"] == "2.49" and snap["regular_usd"] == "12.19"
        # Back-compat tuple wrapper agrees.
        assert resolve_checkout_pricing(sub, target_plan_id=None, billing_cycle_param="monthly")[2] == Decimal("2.49")

    def test_renewal_charges_sale_price(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"))
        sub = _sub(company, p, active=True)
        _completed(sub, _gateway(), Decimal("12.19"))
        q = resolve_checkout_quote(sub, target_plan_id=None, billing_cycle_param="monthly")
        assert q.intent == "renewal" and q.amount_usd == Decimal("2.49")

    def test_upgrade_proration_ignores_sales(self, company):
        silver = _plan(name="Silver", price_monthly=Decimal("30.00"), tier=1)
        gold = _plan(
            name="Gold", price_monthly=Decimal("50.00"), tier=2, sale_price_monthly=Decimal("31.00")
        )
        sub = _sub(company, silver, active=True, days_left=15)
        _completed(sub, _gateway(), Decimal("30.00"))
        q = resolve_checkout_quote(sub, target_plan_id=gold.id, billing_cycle_param="monthly")
        assert q.intent == "upgrade"
        # (50 - 30) * 15/30 = 10.00 on regular prices; a sale-based delta would be 0.50.
        assert Decimal("9.50") <= q.amount_usd <= Decimal("10.50")
        assert q.discount_usd == 0

    def test_preview_exposes_discount(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"), sale_ends_at=timezone.now() + timedelta(days=3))
        sub = _sub(company, p, active=False)
        data = preview_plan_change(sub, plan_id=p.id, billing_cycle_param="monthly")
        assert data["ok"] and data["amount_usd"] == "2.49"
        assert data["regular_amount_usd"] == "12.19" and data["discount_usd"] == "9.70"
        assert data["sale_ends_at"]


@pytest.mark.django_db
class TestPriceLock:
    def _pending(self, sub, plan, gw, quote_snapshot):
        return Payment.objects.create(
            subscription=sub,
            amount=Decimal(quote_snapshot["charged_usd"]),
            currency="USD",
            amount_usd=Decimal(quote_snapshot["charged_usd"]),
            payment_method=gw,
            payment_status=PaymentStatus.COMPLETED.value,
            tran_ref="locked",
            target_plan=plan,
            billing_cycle=BillingCycle.MONTHLY,
            pricing=quote_snapshot,
        )

    def test_webhook_after_sale_ended_still_applies(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"))
        sub = _sub(company, p, active=False)
        snap = resolve_checkout_quote(sub, target_plan_id=None, billing_cycle_param="monthly").snapshot()
        pay = self._pending(sub, p, _gateway(), snap)
        # Sale ends before the gateway confirms.
        p.sale_ends_at = timezone.now() - timedelta(seconds=1)
        p.save()
        finalize_completed_payment(sub, pay, 2.49)
        sub.refresh_from_db()
        assert sub.is_active
        pay.refresh_from_db()
        assert pay.applied_at is not None

    def test_sale_amount_without_lock_is_rejected_after_sale(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"), sale_ends_at=timezone.now() - timedelta(days=1))
        sub = _sub(company, p, active=False)
        with pytest.raises(ValueError):
            apply_successful_payment(sub, amount_usd=2.49, target_plan=p, billing_cycle="monthly")

    def test_legacy_payment_at_regular_price_applies_during_sale(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"))
        sub = _sub(company, p, active=False)
        apply_successful_payment(sub, amount_usd=12.19, target_plan=p, billing_cycle="monthly")
        sub.refresh_from_db()
        assert sub.is_active

    def test_invoice_records_discount(self, company):
        p = _plan(sale_price_monthly=Decimal("2.49"))
        sub = _sub(company, p, active=False)
        snap = resolve_checkout_quote(sub, target_plan_id=None, billing_cycle_param="monthly").snapshot()
        pay = self._pending(sub, p, _gateway(), snap)
        inv = ensure_invoice_for_payment(pay)
        assert inv.amount == Decimal("2.49")
        assert inv.regular_amount == Decimal("12.19")
        assert inv.discount_amount == Decimal("9.70")

        # The rendered PDF itemizes it: list price, discount row, charged total.
        import io

        from pypdf import PdfReader

        from subscriptions.invoicing.pdf import render_invoice_pdf

        text = PdfReader(io.BytesIO(render_invoice_pdf(inv))).pages[0].extract_text()
        assert "Subtotal" in text and "$12.19" in text
        assert "Discount" in text and "-$9.70" in text
        assert "$2.49" in text


@pytest.mark.django_db
class TestCheckoutEndpoint:
    def test_session_charges_sale_and_snapshots(self, api_client, company, owner_user):
        owner_user.phone_verified = True
        owner_user.save(update_fields=["phone_verified"])
        p = _plan(sale_price_monthly=Decimal("2.49"))
        sub = _sub(company, p, active=False)
        _gateway("Stripe")
        api_client.force_authenticate(user=owner_user)
        with patch(
            "subscriptions.stripe_utils.create_stripe_payment_session",
            return_value={"session_id": "cs_sale", "url": "https://checkout.stripe.test/pay"},
        ) as stub:
            res = api_client.post(
                "/api/payments/create-stripe-session/",
                # A client-sent amount must be ignored.
                {"subscription_id": sub.id, "amount": "0.01"},
                format="json",
            )
        assert res.status_code == 200, res.content
        pay = Payment.objects.get(tran_ref="cs_sale")
        assert pay.amount_usd == Decimal("2.49")
        assert pay.pricing["charged_usd"] == "2.49"
        assert pay.pricing["regular_usd"] == "12.19"
        assert stub.called

    def test_pending_session_not_reused_after_price_change(self, api_client, company, owner_user):
        owner_user.phone_verified = True
        owner_user.save(update_fields=["phone_verified"])
        p = _plan()
        sub = _sub(company, p, active=False)
        _gateway("Stripe")
        api_client.force_authenticate(user=owner_user)
        with patch(
            "subscriptions.stripe_utils.create_stripe_payment_session",
            side_effect=[
                {"session_id": "cs_a", "url": "https://checkout.stripe.test/a"},
                {"session_id": "cs_b", "url": "https://checkout.stripe.test/b"},
            ],
        ):
            api_client.post("/api/payments/create-stripe-session/", {"subscription_id": sub.id}, format="json")
            p.sale_price_monthly = Decimal("2.49")
            p.save()
            api_client.post("/api/payments/create-stripe-session/", {"subscription_id": sub.id}, format="json")
        assert Payment.objects.filter(subscription=sub).count() == 2
        assert Payment.objects.get(tran_ref="cs_b").amount_usd == Decimal("2.49")


@pytest.fixture
def super_admin(db):
    from accounts.models import User

    return User.objects.create_user(
        username="root_admin", email="root@example.com", password="Validpass1", is_superuser=True, is_staff=True
    )


@pytest.mark.django_db
class TestPlanApi:
    def test_public_plans_shape_and_order(self, api_client):
        a = _plan(name="A", price_monthly=Decimal("5.00"), sort_order=2)
        b = _plan(
            name="B",
            price_monthly=Decimal("9.00"),
            sale_price_monthly=Decimal("4.50"),
            sale_ends_at=timezone.now() + timedelta(days=2),
            badge="most_popular",
            is_featured=True,
            sort_order=1,
        )
        res = api_client.get("/api/public/plans/")
        assert res.status_code == 200
        rows = api_body(res)
        assert [r["id"] for r in rows] == [b.id, a.id]
        top = rows[0]
        # Existing fields keep their meaning (regular price).
        assert Decimal(top["price_monthly"]) == Decimal("9.00")
        assert top["pricing"]["monthly"] == {
            "regular": "9.00", "price": "4.50", "on_sale": True, "discount_percent": 50
        }
        assert top["badge"] == {"key": "most_popular", "text": "", "text_ar": ""}
        assert top["is_featured"] is True and top["sale_ends_at"]
        assert rows[1]["badge"] is None and rows[1]["sale_ends_at"] is None

    def test_admin_rejects_sale_not_below_regular(self, api_client, super_admin):
        p = _plan()
        api_client.force_authenticate(user=super_admin)
        res = api_client.patch(f"/api/plans/{p.id}/", {"sale_price_monthly": "20.00"}, format="json")
        assert res.status_code == 400
        assert "sale_price_monthly" in str(res.content)

    def test_admin_rejects_sale_on_free_plan(self, api_client, super_admin):
        p = _plan(price_monthly=Decimal("0"), price_yearly=Decimal("0"))
        api_client.force_authenticate(user=super_admin)
        res = api_client.patch(f"/api/plans/{p.id}/", {"sale_price_monthly": "1.00"}, format="json")
        assert res.status_code == 400

    def test_admin_custom_badge_needs_both_locales(self, api_client, super_admin):
        p = _plan()
        api_client.force_authenticate(user=super_admin)
        res = api_client.patch(
            f"/api/plans/{p.id}/", {"badge": "custom", "badge_text": "Hot"}, format="json"
        )
        assert res.status_code == 400
        assert "badge_text_ar" in str(res.content)

    def test_admin_sets_and_clears_sale(self, api_client, super_admin):
        p = _plan()
        api_client.force_authenticate(user=super_admin)
        ends = (timezone.now() + timedelta(days=5)).isoformat()
        res = api_client.patch(
            f"/api/plans/{p.id}/",
            {"sale_price_monthly": "2.49", "sale_ends_at": ends, "badge": "best_value"},
            format="json",
        )
        assert res.status_code == 200, res.content
        p.refresh_from_db()
        assert p.sale_price_monthly == Decimal("2.49") and p.badge == "best_value"
        res = api_client.patch(f"/api/plans/{p.id}/", {"sale_price_monthly": None}, format="json")
        assert res.status_code == 200, res.content
        p.refresh_from_db()
        assert p.sale_price_monthly is None

    def test_admin_list_exposes_sale_status(self, api_client, super_admin):
        _plan(sale_price_monthly=Decimal("2.00"), sale_starts_at=timezone.now() + timedelta(days=1))
        api_client.force_authenticate(user=super_admin)
        res = api_client.get("/api/plans/")
        body = api_body(res)
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        assert rows[0]["sale_status"] == "scheduled"
        assert rows[0]["pricing"]["monthly"]["on_sale"] is False
