"""
Shared fixtures for the telephony tests: ``from tests.telephony_fixtures import *``.

Two companies (A = ``company``, B = ``other_company`` from the root conftest), both
on a plan that includes telephony, plus one PBX node.
"""

import json
import threading
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from django.utils import timezone
from rest_framework.test import APIClient

__all__ = [
    "fernet_key",
    "telephony_plan",
    "telephony_subscription",
    "other_telephony_subscription",
    "pbx_node",
    "admin_client",
    "other_admin_client",
    "employee_client",
    "superadmin_client",
    "tenant_a",
    "tenant_b",
    "make_trunk",
    "make_number",
    "make_extension",
    "FakeWorker",
    "worker",
]


@pytest.fixture(autouse=True)
def fernet_key(settings):
    """Every telephony test gets its own encryption key (no dependency on .env)."""
    import integrations.encryption as enc

    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    enc._fernet_instance = None
    yield
    enc._fernet_instance = None


@pytest.fixture
def telephony_plan(db):
    from subscriptions.models import Plan

    return Plan.objects.create(
        name="PBX",
        description="Plan with telephony",
        price_monthly=Decimal("10"),
        price_yearly=Decimal("100"),
        features={"telephony": True},
        limits={"max_pbx_extensions": 5, "max_pbx_trunks": 2},
    )


def _subscribe(company, plan):
    from subscriptions.models import BillingCycle, Subscription

    now = timezone.now()
    return Subscription.objects.create(
        company=company,
        plan=plan,
        is_active=True,
        start_date=now,
        end_date=now + timedelta(days=30),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
    )


@pytest.fixture
def telephony_subscription(company, telephony_plan):
    return _subscribe(company, telephony_plan)


@pytest.fixture
def other_telephony_subscription(other_company, telephony_plan):
    return _subscribe(other_company, telephony_plan)


@pytest.fixture
def pbx_node(db):
    from telephony.services.provisioning import register_node

    node, node_token, crm_token = register_node(name="pbx-1", region="iq", control_url="http://10.77.0.2:7070")
    node.plain_token = node_token
    node.plain_crm_token = crm_token
    return node


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def admin_client(admin_user, telephony_subscription):
    return _client(admin_user)


@pytest.fixture
def other_admin_client(other_admin_user, other_telephony_subscription):
    return _client(other_admin_user)


@pytest.fixture
def employee_client(employee_user, telephony_subscription):
    return _client(employee_user)


@pytest.fixture
def superadmin_client(db):
    from accounts.models import User

    user = User.objects.create_user(
        username="platform_admin", email="platform@test.com", password="x", role="super_admin",
        is_superuser=True, is_staff=True,
    )
    return _client(user)


@pytest.fixture
def tenant_a(company, pbx_node):
    from telephony.models import PbxTenant

    return PbxTenant.objects.create(company=company, realm_key="dev4test", home_node=pbx_node)


@pytest.fixture
def tenant_b(other_company, pbx_node):
    from telephony.models import PbxTenant

    return PbxTenant.objects.create(company=other_company, realm_key="acme2pbx", home_node=pbx_node)


@pytest.fixture
def make_trunk():
    def _make(company, **kw):
        from telephony.models import PbxTrunk

        defaults = {"name": "Carrier", "host": "192.0.2.10", "allowed_sources": ["192.0.2.10/32"]}
        defaults.update(kw)
        return PbxTrunk.objects.create(company=company, **defaults)

    return _make


@pytest.fixture
def make_number():
    def _make(company, trunk, e164, verified=True, **kw):
        from telephony.models import PbxNumber

        return PbxNumber.objects.create(
            company=company, trunk=trunk, e164=e164, verified_at=timezone.now() if verified else None, **kw
        )

    return _make


@pytest.fixture
def make_extension():
    def _make(company, number, secret="dev-only-secret-0000", **kw):
        from integrations.encryption import encrypt_token
        from telephony.models import PbxExtension

        return PbxExtension.objects.create(
            company=company, number=number, sip_secret_encrypted=encrypt_token(secret) if secret else "", **kw
        )

    return _make


# ---- a node worker (control API) on 127.0.0.1 ---------------------------------------


class FakeWorker:
    def __init__(self):
        self.requests: list[tuple[str, dict, dict]] = []
        self.reply = (202, None)  # None: echo a contract-shaped success
        self.replies: dict[str, tuple[int, dict]] = {}  # path -> (status, body), checked first
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                worker.requests.append((self.path, dict(self.headers), body))
                status, payload = worker.replies.get(self.path, worker.reply)
                if payload is None:
                    payload = {"call_id": body.get("call_id") or self.path.split("/")[3],
                               "status": "originating" if self.path.endswith("originate") else "hanging_up"}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # GET /v1/health
                worker.requests.append((self.path, dict(self.headers), None))
                status, payload = worker.replies.get(
                    self.path, (200, {"ari": True, "redis": True, "buffer_depth": 0, "oldest_unsent_s": 0.0}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def originates(self):
        return [b for p, _, b in self.requests if p == "/v1/calls/originate"]


@pytest.fixture
def worker(pbx_node):
    w = FakeWorker()
    pbx_node.control_url = w.url
    pbx_node.save(update_fields=["control_url"])
    yield w
    w.server.shutdown()
