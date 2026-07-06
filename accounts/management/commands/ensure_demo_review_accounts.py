"""
Create or update store-review demo accounts (Google Play, App Store, Meta App Review).

Each account gets a company, active subscription, verified email/phone, and a fixed 2FA code
from DEMO_*_ACCOUNT_2FA_CODE in settings (same behavior as Google/Apple reviewers).
"""
from __future__ import annotations

import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from companies.models import Company
from subscriptions.entitlements_catalog import DEFAULT_FEATURES
from subscriptions.models import BillingCycle, Plan, Subscription

PLATFORMS = ("google", "apple", "meta")

PLATFORM_LABELS = {
    "google": "Google Play",
    "apple": "App Store",
    "meta": "Meta App Review",
}

DEFAULTS = {
    "google": {
        "username": "google_reviewer",
        "email": "google-reviewer@loop-crm.app",
        "company_name": "Google Play Review Demo",
        "domain": "google-reviewer.review.loop-crm.app",
    },
    "apple": {
        "username": "apple_reviewer",
        "email": "apple-reviewer@loop-crm.app",
        "company_name": "App Store Review Demo",
        "domain": "apple-reviewer.review.loop-crm.app",
    },
    "meta": {
        "username": "meta_reviewer",
        "email": "meta-reviewer@loop-crm.app",
        "company_name": "Meta App Review Demo",
        "domain": "meta-reviewer.review.loop-crm.app",
    },
}


def _setting(kind: str, field: str) -> str:
    key = f"DEMO_{kind.upper()}_ACCOUNT_{field.upper()}"
    return (getattr(settings, key, "") or os.getenv(key, "") or "").strip()


def _password(kind: str, override: str | None) -> str:
    if override:
        return override
    key = f"DEMO_{kind.upper()}_ACCOUNT_PASSWORD"
    value = (os.getenv(key, "") or "").strip()
    if not value:
        raise CommandError(
            f"Set {key} in the environment or pass --password when creating {kind} demo account."
        )
    return value


def _resolve_identity(kind: str) -> tuple[str, str]:
    defaults = DEFAULTS[kind]
    username = _setting(kind, "username") or defaults["username"]
    email = _setting(kind, "email") or defaults["email"]
    return username, email


def _get_or_create_review_plan(kind: str) -> Plan:
    plan_name = f"{PLATFORM_LABELS[kind]} Plan"
    plan = Plan.objects.filter(name=plan_name).order_by("id").first()
    if plan:
        return plan
    return Plan.objects.create(
        name=plan_name,
        description=f"Internal plan for {PLATFORM_LABELS[kind]} demo workspace.",
        price_monthly=0,
        price_yearly=0,
        trial_days=0,
        tier=99,
        users="unlimited",
        clients="unlimited",
        features=dict(DEFAULT_FEATURES),
        visible=False,
    )


def _ensure_active_subscription(company: Company, plan: Plan) -> Subscription:
    now = timezone.now()
    sub = (
        Subscription.objects.filter(company=company, is_active=True)
        .order_by("-created_at")
        .first()
    )
    if sub:
        updates = []
        if sub.plan_id != plan.id:
            sub.plan = plan
            updates.append("plan")
        if sub.end_date <= now:
            sub.end_date = now + timedelta(days=3650)
            updates.append("end_date")
        if not sub.current_period_start:
            sub.current_period_start = now
            updates.append("current_period_start")
        if updates:
            sub.save(update_fields=updates + ["updated_at"])
        return sub

    return Subscription.objects.create(
        company=company,
        plan=plan,
        start_date=now,
        end_date=now + timedelta(days=3650),
        current_period_start=now,
        billing_cycle=BillingCycle.MONTHLY,
        is_active=True,
        auto_renew=False,
    )


@transaction.atomic
def ensure_demo_review_account(kind: str, *, password: str) -> dict:
    if kind not in PLATFORMS:
        raise CommandError(f"Unknown platform: {kind}")

    User = get_user_model()
    defaults = DEFAULTS[kind]
    username, email = _resolve_identity(kind)
    company_name = defaults["company_name"]
    domain = defaults["domain"]

    user = User.objects.filter(username__iexact=username).first()
    if user is None and email:
        user = User.objects.filter(email__iexact=email).first()

    created_user = False
    if user is None:
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=PLATFORM_LABELS[kind].split()[0],
            last_name="Reviewer",
            role=Role.ADMIN.value,
        )
        created_user = True
    else:
        user.set_password(password)
        user.email = email
        user.role = Role.ADMIN.value
        user.is_active = True

    user.email_verified = True
    user.phone_verified = True
    if not user.phone:
        user.phone = "+10000000000"
    user.save()

    company = Company.objects.filter(owner=user).order_by("id").first()
    created_company = False
    if company is None:
        if Company.objects.filter(domain=domain).exists():
            domain = f"{username}.review.loop-crm.app"
        company = Company.objects.create(
            name=company_name,
            domain=domain,
            specialization="services",
            owner=user,
        )
        created_company = True

    user.company = company
    user.save(update_fields=["company"])

    plan = _get_or_create_review_plan(kind)
    subscription = _ensure_active_subscription(company, plan)

    two_fa = _setting(kind, "2fa_code")
    return {
        "platform": kind,
        "created_user": created_user,
        "created_company": created_company,
        "username": user.username,
        "email": user.email,
        "password": password,
        "two_fa_code": two_fa or "(set DEMO_*_ACCOUNT_2FA_CODE in .env)",
        "company_id": company.id,
        "company_name": company.name,
        "subscription_id": subscription.id,
        "plan": plan.name,
    }


class Command(BaseCommand):
    help = "Create or update Google Play / App Store / Meta App Review demo accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform",
            choices=[*PLATFORMS, "all"],
            default="meta",
            help="Which demo account to ensure (default: meta).",
        )
        parser.add_argument(
            "--password",
            help="Password for the demo user (overrides DEMO_*_ACCOUNT_PASSWORD env).",
        )

    def handle(self, *args, **options):
        platform = options["platform"]
        kinds = list(PLATFORMS) if platform == "all" else [platform]
        password_override = (options.get("password") or "").strip() or None

        for kind in kinds:
            password = _password(kind, password_override)
            result = ensure_demo_review_account(kind, password=password)
            self.stdout.write(self.style.SUCCESS(f"\n{PLATFORM_LABELS[kind]} demo account ready:"))
            self.stdout.write(f"  Username: {result['username']}")
            self.stdout.write(f"  Email:    {result['email']}")
            self.stdout.write(f"  Password: {result['password']}")
            self.stdout.write(f"  2FA code: {result['two_fa_code']}")
            self.stdout.write(f"  Company:  {result['company_name']} (id={result['company_id']})")
            if result["created_user"]:
                self.stdout.write("  (new user created)")
            if result["created_company"]:
                self.stdout.write("  (new company created)")
