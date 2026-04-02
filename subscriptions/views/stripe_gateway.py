import json
import logging
from decimal import Decimal

from django.conf import settings
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from crm_saas_api.responses import error_response, success_response, validation_error_response

from ..models import (
    Plan,
    Subscription,
    Payment,
    PaymentStatus,
    PaymentGateway,
    PaymentGatewayStatus,
    InvoiceStatus,
)
from ..serializers import CreateStripePaymentSerializer
from ..stripe_utils import verify_stripe_payment, create_stripe_payment_session
from ..services.billing import finalize_completed_payment, resolve_checkout_pricing

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([AllowAny])
def create_stripe_payment(request):
    """
    Create a Stripe payment session for a subscription
    POST /api/payments/create-stripe-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: 'monthly' | 'yearly' }
    """
    # Validate serializer
    serializer = CreateStripePaymentSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    subscription_id = serializer.validated_data.get("subscription_id")
    plan_id = serializer.validated_data.get("plan_id")
    billing_cycle_param = serializer.validated_data.get("billing_cycle")
    
    if not subscription_id:
        return error_response(
            "subscription_id is required",
            code="bad_request",
        )

    try:
        subscription = Subscription.objects.select_related("plan").get(id=subscription_id)
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

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

    amount = float(amount_dec)
    logger.info(
        f"Creating Stripe payment for subscription {subscription_id}: "
        f"intent={intent}, plan={target_plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return error_response(
            "Plan is free, no payment required",
            code="bad_request",
        )

    # Prepare Stripe payment request
    user_email = subscription.company.owner.email
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username

    # Stripe success and cancel URLs
    # IMPORTANT: success_url must point to backend stripe_return endpoint
    # so that payment can be verified and subscription updated before redirecting to frontend
    success_url = f"{settings.API_BASE_URL}/api/payments/stripe-return/?subscription_id={subscription_id}&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{settings.FRONTEND_URL}/payment?subscription_id={subscription_id}&canceled=true"
    return_url = f"{settings.API_BASE_URL}/api/payments/stripe-return/?subscription_id={subscription_id}"  # Deprecated, kept for compatibility

    try:
        result = create_stripe_payment_session(
            amount=amount,
            customer_email=user_email,
            customer_name=user_name,
            subscription_id=subscription_id,
            return_url=return_url,
            success_url=success_url,
            cancel_url=cancel_url,
            extra_metadata={
                "target_plan_id": str(target_plan.id),
                "billing_cycle": billing_cycle,
                "intent": intent,
            },
        )

        # Check for url (for Stripe Checkout)
        if result.get("url"):
            # Get Stripe gateway
            stripe_gateway = PaymentGateway.objects.filter(
                name__icontains="stripe",
                enabled=True,
                status=PaymentGatewayStatus.ACTIVE.value,
            ).first()

            if not stripe_gateway:
                return error_response(
                    "Stripe gateway is not configured or enabled",
                    code="bad_request",
                )
            
            # Create payment record (Stripe amount in USD)
            from decimal import Decimal
            session_id = result.get("session_id", "")
            payment = Payment.objects.create(
                subscription=subscription,
                amount=amount,
                currency="USD",
                exchange_rate=Decimal("1"),
                amount_usd=Decimal(str(amount)),
                payment_method=stripe_gateway,
                payment_status=PaymentStatus.PENDING.value,
                tran_ref=session_id,  # Store session_id as tran_ref
                target_plan=target_plan,
                billing_cycle=billing_cycle,
            )

            return success_response(
                data={
                    "payment_id": payment.id,
                    "redirect_url": result.get("url"),
                    "session_id": session_id,
                },
            )
        else:
            # Error response from Stripe
            error_msg = (
                result.get("error")
                or result.get("message")
                or "Failed to create payment session"
            )
            return error_response(str(error_msg), code="bad_request")
    except Exception as e:
        logger.error(f"Error creating Stripe payment: {str(e)}", exc_info=True)
        return error_response(
            f"Error creating Stripe payment: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@csrf_exempt
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def stripe_return(request):
    """
    Handle Stripe return URL - Stripe redirects here after payment
    Stripe sends session_id in query params or we get it from frontend
    This endpoint processes payment and redirects to frontend success page
    """
    logger = logging.getLogger(__name__)
    
    # Log initial request details
    logger.info("=" * 80)
    logger.info("STRIPE RETURN URL CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"GET params: {dict(request.GET)}")
    logger.info(f"POST data: {dict(request.POST) if hasattr(request, 'POST') else 'N/A'}")
    logger.info(f"Request data: {getattr(request, 'data', 'N/A')}")
    logger.info(f"Request body: {request.body.decode('utf-8') if request.body else 'Empty'}")
    logger.info("=" * 80)

    try:
        # Get session_id from query params or request data
        session_id = request.GET.get("session_id") or request.data.get("session_id")
        subscription_id_param = request.GET.get("subscription_id") or request.data.get("subscription_id")

        if not session_id:
            logger.error("Missing session_id in Stripe return URL")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Missing session ID"
            )

        # Verify transaction
        logger.info(f"Attempting to verify Stripe payment with session_id: {session_id}")
        result = verify_stripe_payment(session_id)
        logger.info(f"Stripe verified result: {json.dumps(result, indent=2, default=str)}")

        # Extract subscription from metadata or URL params
        subscription_id = result.get("subscription_id")
        if subscription_id:
            try:
                subscription_id = int(subscription_id)
            except (ValueError, TypeError):
                subscription_id = None

        # If subscription_id not in result, try to get from URL params
        if not subscription_id and subscription_id_param:
            try:
                subscription_id = int(subscription_id_param)
            except (ValueError, TypeError):
                subscription_id = None

        if not subscription_id:
            logger.error("Missing subscription_id in Stripe return")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Invalid transaction"
            )

        subscription = Subscription.objects.get(id=subscription_id)
        logger.info(f"Found subscription: ID={subscription.id}, Company={subscription.company.name if subscription.company else 'None'}, "
                 f"Current end_date={subscription.end_date}, is_active={subscription.is_active}")

        # Update payment status
        payment_status = result.get("payment_status")
        stripe_payment_status = result.get("stripe_payment_status")
        logger.info(f"Payment status: {payment_status}, Stripe payment status: {stripe_payment_status}")
        
        if payment_status == "completed" and stripe_payment_status == "paid":
            logger.info("Payment COMPLETED and PAID - Processing payment and updating subscription...")
            stripe_gateway = PaymentGateway.objects.filter(
                name__icontains="stripe", enabled=True
            ).first()

            amount = float(result.get("amount_total", 0))

            payment = None
            if stripe_gateway:
                payment = Payment.objects.filter(
                    subscription=subscription,
                    tran_ref=session_id,
                    payment_method=stripe_gateway,
                ).first()
                if not payment:
                    payment = (
                        Payment.objects.filter(
                            subscription=subscription, payment_method=stripe_gateway
                        )
                        .order_by("-created_at")
                        .first()
                    )

                if payment:
                    logger.info(
                        "Updating existing payment: ID=%s, status=%s",
                        payment.id,
                        payment.payment_status,
                    )
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = session_id
                    payment.amount = amount
                    payment.currency = "USD"
                    payment.exchange_rate = Decimal("1")
                    payment.amount_usd = Decimal(str(amount))
                    payment.save()
                else:
                    logger.info(
                        "Creating new payment: subscription_id=%s amount=%s",
                        subscription.id,
                        amount,
                    )
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        currency="USD",
                        exchange_rate=Decimal("1"),
                        amount_usd=Decimal(str(amount)),
                        payment_method=stripe_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=session_id,
                    )

            if payment:
                try:
                    finalize_completed_payment(subscription, payment, amount)
                    subscription.refresh_from_db()
                except ValueError as err:
                    logger.error("Billing apply failed: %s", err, exc_info=True)
                    frontend_url = settings.FRONTEND_URL
                    return redirect(
                        f"{frontend_url}/payment/success?subscription_id={subscription_id}"
                        f"&status=failed&message={str(err)}"
                    )

            logger.info(
                "Activated subscription %s for company %s",
                subscription.id,
                getattr(subscription.company, "name", None),
            )

        # Redirect to frontend success page
        frontend_url = settings.FRONTEND_URL
        if payment_status == "completed":
            logger.info(f"Redirecting to success page: {frontend_url}/payment/success?subscription_id={subscription_id}&status=success&session_id={session_id}")
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=success&session_id={session_id}"
            )
        else:
            logger.warning(f"Payment NOT completed (status: {payment_status}, stripe_status: {stripe_payment_status}). Redirecting to failed page.")
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Payment failed"
            )

    except Subscription.DoesNotExist:
        logger.error(f"Subscription not found in Stripe return handler")
        frontend_url = settings.FRONTEND_URL
        return redirect(
            f"{frontend_url}/payment/success?status=failed&message=Subscription not found"
        )
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"ERROR processing Stripe return: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback:", exc_info=True)
        logger.error("=" * 80)
        frontend_url = settings.FRONTEND_URL
        subscription_id = request.GET.get("subscription_id") or ""
        return redirect(
            f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=error&message={str(e)}"
        )
