"""Promotion bar: live selection, the public read, admin CRUD and validation."""
from datetime import timedelta

import pytest
from django.utils import timezone

from conftest import api_body
from subscriptions.models import Promotion

MESSAGES = [{"en": "Biggest deals of the year", "ar": "أكبر عروض السنة"}]


def _promo(**kw):
    now = timezone.now()
    defaults = dict(
        name="BF",
        messages=MESSAGES,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=1),
        audiences=["public", "dashboard", "mobile"],
    )
    defaults.update(kw)
    return Promotion.objects.create(**defaults)


@pytest.fixture
def super_admin(db):
    from accounts.models import User

    return User.objects.create_user(
        username="promo_root", email="promo_root@example.com", password="Validpass1", is_superuser=True, is_staff=True
    )


@pytest.mark.django_db
class TestLiveSelection:
    def test_window_and_kill_switch(self):
        now = timezone.now()
        _promo(name="ended", ends_at=now - timedelta(minutes=1), starts_at=now - timedelta(days=2))
        _promo(name="scheduled", starts_at=now + timedelta(hours=1))
        _promo(name="paused", is_active=False)
        assert Promotion.objects.current(at=now) is None
        live = _promo(name="live")
        assert Promotion.objects.current(at=now) == live

    def test_priority_then_newest(self):
        _promo(name="low", priority=0)
        high = _promo(name="high", priority=5)
        assert Promotion.objects.current() == high

    def test_audience_filter(self):
        only_mobile = _promo(audiences=["mobile"])
        assert Promotion.objects.current(audience="public") is None
        assert Promotion.objects.current(audience="mobile") == only_mobile

    def test_status_labels(self):
        now = timezone.now()
        assert _promo().status_at(now) == "live"
        assert _promo(is_active=False).status_at(now) == "paused"
        assert _promo(starts_at=now + timedelta(days=1), ends_at=None).status_at(now) == "scheduled"
        assert _promo(starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1)).status_at(now) == "ended"


@pytest.mark.django_db
class TestPublicEndpoint:
    def test_anonymous_read_shape(self, api_client):
        p = _promo(cta_label="See plans", cta_label_ar="الخطط", show_countdown=True)
        res = api_client.get("/api/public/promotions/active/?audience=public")
        assert res.status_code == 200
        assert res["Cache-Control"] == "no-store"
        data = api_body(res)
        assert data["server_time"]
        promo = data["promotion"]
        assert promo["id"] == p.id
        assert promo["messages"] == MESSAGES
        # Internal fields never leak.
        for internal in ("name", "priority", "created_by", "audiences", "is_active"):
            assert internal not in promo

    def test_none_live(self, api_client):
        res = api_client.get("/api/public/promotions/active/")
        assert api_body(res)["promotion"] is None

    def test_unknown_audience_falls_back_to_public(self, api_client):
        _promo(audiences=["public"])
        res = api_client.get("/api/public/promotions/active/?audience=bogus")
        assert api_body(res)["promotion"] is not None


def _payload(**kw):
    now = timezone.now()
    data = {
        "name": "Black Friday",
        "messages": MESSAGES,
        "theme": "sale",
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=3)).isoformat(),
        "show_countdown": True,
        "audiences": ["public", "dashboard"],
        "cta_label": "See plans",
        "cta_label_ar": "الخطط",
        "cta_url": "/register",
    }
    data.update(kw)
    return data


@pytest.mark.django_db
class TestAdminCrud:
    def test_requires_plan_permission(self, api_client, employee_user):
        api_client.force_authenticate(user=employee_user)
        assert api_client.get("/api/promotions/").status_code == 403
        assert api_client.post("/api/promotions/", _payload(), format="json").status_code == 403

    def test_anonymous_rejected(self, api_client):
        assert api_client.get("/api/promotions/").status_code in (401, 403)

    def test_create_list_duplicate_delete(self, api_client, super_admin):
        api_client.force_authenticate(user=super_admin)
        res = api_client.post("/api/promotions/", _payload(), format="json")
        assert res.status_code == 201, res.content
        created = api_body(res)
        assert created["status"] == "live"
        assert Promotion.objects.get(pk=created["id"]).created_by == super_admin

        res = api_client.post(f"/api/promotions/{created['id']}/duplicate/")
        assert res.status_code == 201
        dup = api_body(res)
        assert dup["is_active"] is False and dup["status"] == "paused"

        rows = api_body(api_client.get("/api/promotions/?status=live"))
        assert [r["id"] for r in rows] == [created["id"]]

        assert api_client.delete(f"/api/promotions/{created['id']}/").status_code == 204

    @pytest.mark.parametrize(
        "override,field",
        [
            ({"messages": []}, "messages"),
            ({"messages": [{"en": "Only English", "ar": ""}]}, "messages"),
            ({"messages": [{"en": "x", "ar": "y"}] * 7}, "messages"),
            ({"audiences": []}, "audiences"),
            ({"audiences": ["tv"]}, "audiences"),
            ({"ends_at": None}, "show_countdown"),
            ({"cta_url": "javascript:alert(1)"}, "cta_url"),
            ({"cta_url": "http://insecure.example.com"}, "cta_url"),
            ({"cta_label_ar": ""}, "cta_label_ar"),
            ({"theme": "neon"}, "theme"),
        ],
    )
    def test_validation(self, api_client, super_admin, override, field):
        api_client.force_authenticate(user=super_admin)
        res = api_client.post("/api/promotions/", _payload(**override), format="json")
        assert res.status_code == 400, res.content
        assert field in res.content.decode()

    def test_end_before_start_rejected(self, api_client, super_admin):
        api_client.force_authenticate(user=super_admin)
        now = timezone.now()
        res = api_client.post(
            "/api/promotions/",
            _payload(starts_at=now.isoformat(), ends_at=(now - timedelta(hours=1)).isoformat()),
            format="json",
        )
        assert res.status_code == 400
        assert "ends_at" in res.content.decode()
