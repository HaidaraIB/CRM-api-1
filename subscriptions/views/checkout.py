"""
The generic checkout flow, shared by every gateway.

Each of the five create-session views used to repeat the same ~100-line
prologue - validate, load subscription, check owner, check phone, price the
change, look up the gateway row, reuse a pending session, build the customer
name - differing only in the serializer class name and the log string. The
gateway-specific parts (opening the session, naming the response fields) now
live on the adapter, so this flow exists once.

The per-gateway URLs still resolve here through thin wrappers in each gateway
view module, so the HTTP contract is unchanged.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from rest_framework import status

from crm_saas_api.responses import (
    error_response,
    success_response,
    validation_error_response,
)

from ..gateways.base import CheckoutContext, GatewayError
from ..gateways.registry import resolve_gateway
from ..models import Payment, PaymentStatus, Plan, Subscription
from ..phone_verification_gate import require_owner_phone_verified
from ..serializers import CreateCheckoutSessionSerializer
from ..services.billing import resolve_checkout_pricing
from ..services.checkout_auth import require_subscription_owner
from ..services.payment_completion import (
    attach_checkout_session,
    find_reusable_pending_payment,
)

logger = logging.getLogger(__name__)


def _customer_name(owner) -> str:
    name = f"{owner.first_name} {owner.last_name}".strip()
    return name or owner.username


def _default_urls(slug: str, subscription_id: int) -> tuple[str, str]:
    """(return_url, callback_url) for a gateway, matching the pre-refactor values."""
    api_base = settings.API_BASE_URL.rstrip("/")
    frontend = settings.FRONTEND_URL.rstrip("/")

    if slug == "paytabs":
        return (
            f"{settings.PAYTABS_RETURN_URL}?subscription_id={subscription_id}",
            getattr(settings, "PAYTABS_CALLBACK_URL", "")
            or f"{api_base}/api/payments/paytabs-callback/",
        )
    if slug == "stripe":
        return (
            f"{api_base}/api/payments/stripe-return/?subscription_id={subscription_id}",
            f"{frontend}/payment/success?subscription_id={subscription_id}&status=cancelled",
        )
    if slug == "qicard":
        return (
            f"{api_base}/api/payments/qicard-return/?subscription_id={subscription_id}",
            f"{api_base}/api/payments/qicard-webhook/",
        )
    if slug == "fib":
        return "", f"{api_base}/api/payments/fib-callback/"
    if slug == "zaincash":
        # v2 requires separate successUrl/failureUrl; both point at the same
        # backend endpoint since confirm_and_finalize_payment re-verifies with
        # Zain Cash's inquiry API rather than trusting which redirect fired.
        return f"{api_base}/api/payments/zaincash-return/?subscription_id={subscription_id}", ""
    if slug == "alqaseh":
        return (
            f"{api_base}/api/payments/alqaseh-return/?subscription_id={subscription_id}",
            f"{api_base}/api/payments/alqaseh-webhook/",
        )
    return "", ""


def create_checkout_session(request, *, slug: str):
    """
    Open (or reuse) a hosted checkout session for `slug`.

    POST body: { subscription_id, plan_id?, billing_cycle? }
    """
    serializer = CreateCheckoutSessionSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    subscription_id = serializer.validated_data.get("subscription_id")
    plan_id = serializer.validated_data.get("plan_id")
    billing_cycle_param = serializer.validated_data.get("billing_cycle")

    if not subscription_id:
        return error_response("subscription_id is required", code="bad_request")

    try:
        subscription = Subscription.objects.select_related(
            "plan", "company__owner"
        ).get(id=subscription_id)
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    owner_err = require_subscription_owner(request, subscription)
    if owner_err is not None:
        return owner_err

    gate = require_owner_phone_verified(subscription)
    if gate is not None:
        return gate

    try:
        target_plan, billing_cycle, amount_dec, intent = resolve_checkout_pricing(
            subscription,
            target_plan_id=plan_id,
            billing_cycle_param=billing_cycle_param,
        )
    except ValueError as e:
        return error_response(str(e), code="invalid_checkout")
    except Plan.DoesNotExist:
        return error_response(
            "Plan not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return error_response(
            "Subscription is already active. Use renewal or plan change to proceed.",
            code="bad_request",
        )

    if amount_dec <= 0:
        return error_response("Plan is free, no payment required", code="bad_request")

    gateway_row, adapter = resolve_gateway(slug)
    if adapter is None:
        return error_response(f"Unknown payment gateway '{slug}'", code="bad_request")
    if gateway_row is None:
        return error_response(
            f"{slug} gateway is not configured or enabled", code="bad_request"
        )

    logger.info(
        "Creating %s checkout for subscription %s: intent=%s plan=%s cycle=%s amount=%s",
        slug,
        subscription_id,
        intent,
        target_plan.name,
        billing_cycle,
        amount_dec,
    )

    owner = subscription.company.owner
    return_url, callback_url = _default_urls(slug, subscription_id)

    # The reuse lookup and the row insert have to be one critical section. A
    # double-submitting client sends two create-session requests milliseconds
    # apart; without the lock both read "nothing reusable" before either has
    # written its Payment row, and the user ends up with two gateway sessions
    # and two invoices. Locking the subscription row makes the second request
    # wait and then find the first one's pending payment.
    #
    # This holds the transaction open across the gateway HTTP call, which is
    # bounded by the adapter's request timeout and only ever blocks another
    # checkout for the *same* subscription — which is exactly what we want.
    try:
        with transaction.atomic():
            Subscription.objects.select_for_update().filter(pk=subscription.pk).first()

            reusable = find_reusable_pending_payment(
                subscription=subscription,
                gateway=gateway_row,
                target_plan=target_plan,
                billing_cycle=billing_cycle,
                amount_usd=amount_dec,
            )
            if reusable is not None:
                logger.info("Reusing pending %s session payment=%s", slug, reusable.id)
                return success_response(
                    data=adapter.session_payload(
                        reusable, adapter.session_from_payment(reusable)
                    )
                )

            ctx = CheckoutContext(
                subscription=subscription,
                target_plan=target_plan,
                billing_cycle=billing_cycle,
                amount_usd=amount_dec,
                customer_email=owner.email,
                customer_name=_customer_name(owner),
                customer_phone=owner.phone or "",
                return_url=return_url,
                callback_url=callback_url,
            )
            session = adapter.create_session(ctx)

            payment = Payment.objects.create(
                subscription=subscription,
                amount=float(amount_dec),
                currency="USD",
                exchange_rate=Decimal("1"),
                amount_usd=Decimal(str(amount_dec)),
                payment_method=gateway_row,
                payment_status=PaymentStatus.PENDING.value,
                tran_ref=session.tran_ref,
                target_plan=target_plan,
                billing_cycle=billing_cycle,
            )
            attach_checkout_session(
                payment,
                tran_ref=session.tran_ref,
                checkout_url=session.checkout_url,
                session_expires_at=session.expires_at,
                session_meta=session.meta or None,
            )
    except GatewayError as e:
        logger.error("%s create session failed: %s", slug, e.message)
        return error_response(
            e.message,
            code="bad_request",
            details=e.details,
            status_code=e.status_code or status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.exception("Unexpected error creating %s session", slug)
        return error_response(
            f"Unexpected error: {e}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    logger.info(
        "Created %s payment record payment_id=%s tran_ref=%s subscription_id=%s",
        slug,
        payment.id,
        session.tran_ref,
        subscription_id,
    )

    return success_response(data=adapter.session_payload(payment, session))
