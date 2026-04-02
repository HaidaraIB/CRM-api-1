import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone
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
        subscription = Subscription.objects.get(id=subscription_id)
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # If plan_id is provided, update the subscription's plan (for plan changes)
    if plan_id:
        try:
            new_plan = Plan.objects.get(id=plan_id)
            subscription.plan = new_plan
            subscription.save(update_fields=['plan', 'updated_at'])
            logger.info(
                f"Updated subscription {subscription_id} plan to {new_plan.name} (ID: {plan_id})"
            )
        except Plan.DoesNotExist:
            return error_response(
                "Plan not found",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    # Allow renewals and plan changes even if subscription is active
    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return error_response(
            "Subscription is already active. Use renewal or plan change to proceed.",
            code="bad_request",
        )

    plan = subscription.plan

    # Determine billing cycle
    if billing_cycle_param:
        billing_cycle = billing_cycle_param
        logger.info(
            f"Using provided billing_cycle: {billing_cycle} for subscription {subscription_id}"
        )
    else:
        days_diff = (subscription.end_date - subscription.start_date).days
        billing_cycle = "yearly" if days_diff >= 330 else "monthly"
        logger.info(
            f"Calculated billing_cycle from subscription: {billing_cycle} "
            f"(days_diff={days_diff}) for subscription {subscription_id}"
        )
    
    # Get the correct amount based on billing cycle
    amount = float(
        plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly
    )
    
    logger.info(
        f"Creating Stripe payment for subscription {subscription_id}: "
        f"plan={plan.name}, billing_cycle={billing_cycle}, amount={amount}"
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
            # Get Stripe gateway
            stripe_gateway = PaymentGateway.objects.filter(
                name__icontains="stripe", enabled=True
            ).first()

            amount = float(result.get("amount_total", 0))
            
            # Find existing payment or create new one
            payment = None
            if stripe_gateway:
                payment = (
                    Payment.objects.filter(
                        subscription=subscription, payment_method=stripe_gateway
                    )
                    .order_by("-created_at")
                    .first()
                )

                if payment:
                    # Update existing payment (Stripe amounts in USD)
                    from decimal import Decimal
                    logger.info(f"Updating existing payment: ID={payment.id}, Old status={payment.payment_status}, "
                               f"Old amount={payment.amount}, Old tran_ref={payment.tran_ref}")
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = session_id
                    payment.amount = amount
                    payment.currency = "USD"
                    payment.exchange_rate = Decimal("1")
                    payment.amount_usd = Decimal(str(amount))
                    payment.save()
                    logger.info(f"Payment updated successfully: ID={payment.id}, New status={payment.payment_status}, "
                               f"New amount={payment.amount}, New tran_ref={payment.tran_ref}")
                else:
                    # Create new payment (Stripe in USD)
                    from decimal import Decimal
                    logger.info(f"Creating new payment: subscription_id={subscription.id}, amount={amount} USD, "
                               f"gateway={stripe_gateway.name if stripe_gateway else 'None'}, session_id={session_id}")
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
                    logger.info(f"Payment created successfully: ID={payment.id}, status={payment.payment_status}")
            
            # Determine billing cycle based on payment amount (Stripe amount in USD)
            plan = subscription.plan
            billing_cycle = None
            
            if abs(amount - float(plan.price_yearly)) < 0.01:
                billing_cycle = "yearly"
            elif abs(amount - float(plan.price_monthly)) < 0.01:
                billing_cycle = "monthly"
            else:
                yearly_diff = abs(amount - float(plan.price_yearly))
                monthly_diff = abs(amount - float(plan.price_monthly))
                billing_cycle = "yearly" if yearly_diff < monthly_diff else "monthly"
                logger.warning(
                    f"Payment amount {amount} doesn't exactly match plan prices "
                    f"(yearly: {plan.price_yearly}, monthly: {plan.price_monthly}). "
                    f"Using {billing_cycle} based on closest match."
                )
            
            # Calculate correct end_date based on billing cycle
            now = timezone.now()
            
            # Check if this is a new subscription (first payment) or renewal
            # Exclude the current payment we just created/updated
            payment_id_to_exclude = payment.id if payment else None
            has_completed_payments = Payment.objects.filter(
                subscription=subscription,
                payment_status=PaymentStatus.COMPLETED.value
            )
            if payment_id_to_exclude:
                has_completed_payments = has_completed_payments.exclude(id=payment_id_to_exclude)
            has_completed_payments = has_completed_payments.exists()
            
            # Determine base_date based on subscription state:
            # 1. Renewal: has completed payments AND end_date is in the future -> extend from end_date
            # 2. Reactivation: has completed payments BUT end_date is in the past -> start from now
            # 3. New subscription: no completed payments -> start from now or start_date
            if has_completed_payments and subscription.end_date > now:
                # This is a renewal - extend from existing end_date
                base_date = subscription.end_date
                logger.info(
                    f"Subscription {subscription.id} is a RENEWAL - extending from end_date ({subscription.end_date.strftime('%Y-%m-%d %H:%M:%S')})"
                )
            elif has_completed_payments and subscription.end_date <= now:
                # This is a reactivation - subscription expired, start from now
                base_date = now
                logger.info(
                    f"Subscription {subscription.id} is a REACTIVATION (expired) - starting from now ({base_date.strftime('%Y-%m-%d %H:%M:%S')})"
                )
            else:
                # This is a new subscription (first payment) - start from now or start_date
                if subscription.start_date and subscription.start_date > now:
                    base_date = subscription.start_date
                    logger.info(
                        f"Subscription {subscription.id} is NEW - using start_date ({subscription.start_date.strftime('%Y-%m-%d %H:%M:%S')})"
                    )
                else:
                    base_date = now
                    logger.info(
                        f"Subscription {subscription.id} is NEW - using now ({base_date.strftime('%Y-%m-%d %H:%M:%S')})"
                    )
            
            if billing_cycle == "yearly":
                new_end_date = base_date + timedelta(days=365)
            else:
                new_end_date = base_date + timedelta(days=30)
            
            # Update subscription with correct end_date and activate it
            old_is_active = subscription.is_active
            old_end_date = subscription.end_date
            logger.info(f"BEFORE UPDATE - Subscription {subscription.id}: is_active={old_is_active}, "
                       f"end_date={old_end_date.strftime('%Y-%m-%d %H:%M:%S') if old_end_date else 'None'}")
            
            subscription.is_active = True
            subscription.end_date = new_end_date
            subscription.save(update_fields=['is_active', 'end_date', 'updated_at'])
            
            # Refresh from DB to verify update
            subscription.refresh_from_db()
            logger.info(f"AFTER UPDATE - Subscription {subscription.id}: is_active={subscription.is_active}, "
                       f"end_date={subscription.end_date.strftime('%Y-%m-%d %H:%M:%S') if subscription.end_date else 'None'}")
            
            logger.info(
                f"✓ Activated subscription {subscription.id} for company {subscription.company.name if subscription.company else 'None'}. "
                f"Billing cycle: {billing_cycle}, Amount: {amount}, "
                f"End date set to: {new_end_date.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # Mark company registration as completed
            company = subscription.company
            if company:
                company.registration_completed = True
                company.registration_completed_at = timezone.now()
                company.save()
                logger.info(f"Marked company {company.id} ({company.name}) registration as completed")

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
