import json
import logging
import requests
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
    PaymentGateway,
    PaymentStatus,
    PaymentGatewayStatus,
)
from ..serializers import CreatePaytabsPaymentSerializer
from ..paytabs_utils import verify_paytabs_payment, create_paytabs_payment_session
from ..services.billing import finalize_completed_payment, resolve_checkout_pricing
from ..phone_verification_gate import require_owner_phone_verified

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([AllowAny])
def create_paytabs_payment(request):
    """
    Create a Paytabs payment session for a subscription
    POST /api/payments/create-paytabs-session/
    Body: { subscription_id: int }
    """
    # Validate serializer
    serializer = CreatePaytabsPaymentSerializer(data=request.data)
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
        subscription = Subscription.objects.select_related("plan", "company__owner").get(
            id=subscription_id
        )
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

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

    amount = float(amount_dec)
    logger.info(
        f"Creating PayTabs payment for subscription {subscription_id}: "
        f"intent={intent}, plan={target_plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return error_response(
            "Plan is free, no payment required",
            code="bad_request",
        )

    # Prepare Paytabs payment request
    user_email = subscription.company.owner.email
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username

    return_url = f"{settings.PAYTABS_RETURN_URL}?subscription_id={subscription_id}"

    customer_phone = subscription.company.owner.phone or ""

    try:
        # Make request to Paytabs
        # Note: Paytabs expects application/octet-stream and data=json.dumps(), not json=payload

        result = create_paytabs_payment_session(
            amount=amount,
            customer_email=user_email,
            customer_name=user_name,
            customer_phone=customer_phone,
            subscription_id=subscription_id,
            return_url=return_url,
        )

        # Check for redirect_url (for hosted payment page)
        if result.get("redirect_url"):
            # Get Paytabs gateway
            paytabs_gateway = PaymentGateway.objects.filter(
                name__icontains="paytabs",
                enabled=True,
                status=PaymentGatewayStatus.ACTIVE.value,
            ).first()

            if not paytabs_gateway:
                return error_response(
                    "Paytabs gateway is not configured or enabled",
                    code="bad_request",
                )
            # Create payment record (amount in USD at creation; callback will update to IQD if PayTabs charges in IQD)
            tran_ref = result.get("tran_ref", "")
            from decimal import Decimal
            payment = Payment.objects.create(
                subscription=subscription,
                amount=amount,
                currency="USD",
                exchange_rate=Decimal("1"),
                amount_usd=Decimal(str(amount)),
                payment_method=paytabs_gateway,  # Use ForeignKey
                payment_status=PaymentStatus.PENDING.value,
                tran_ref=tran_ref,  # Store tran_ref
                target_plan=target_plan,
                billing_cycle=billing_cycle,
            )

            return success_response(
                data={
                    "payment_id": payment.id,
                    "redirect_url": result.get("redirect_url"),
                    "tran_ref": tran_ref,
                },
            )
        else:
            # Error response from Paytabs
            error_msg = (
                result.get("message")
                or result.get("error")
                or "Failed to create payment session"
            )
            return error_response(error_msg, code="bad_request")
    except requests.exceptions.HTTPError as e:
        # Try to get error details from response
        error_details = {}
        try:
            error_details = e.response.json()
        except:
            error_details = {"message": e.response.text}

        return error_response(
            f"Paytabs API error: {error_details.get('message', str(e))}",
            code="bad_request",
            details=error_details,
        )
    except requests.exceptions.RequestException as e:
        return error_response(
            f"Error communicating with Paytabs: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except Exception as e:
        return error_response(
            f"Unexpected error: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def paytabs_return(request):
    """
    Handle Paytabs return URL - PayTabs redirects here after payment (like in uchat_paytabs_gateway)
    PayTabs sends data in request.body (POST) or query params (GET)
    This endpoint processes payment and redirects to frontend success page
    """
    logger = logging.getLogger(__name__)
    
    # Read request.body FIRST before accessing request.POST (which consumes the stream)
    request_body_str = None
    try:
        if request.body:
            request_body_str = request.body.decode('utf-8')
    except Exception as e:
        logger.warning(f"Could not read request body: {str(e)}")
        request_body_str = None
    
    # Log initial request details
    logger.info("=" * 80)
    logger.info("PAYTABS RETURN URL CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"GET params: {dict(request.GET)}")
    logger.info(f"POST data: {dict(request.POST) if hasattr(request, 'POST') else 'N/A'}")
    logger.info(f"Request body: {request_body_str if request_body_str else 'Empty'}")
    logger.info("=" * 80)

    try:
        # Parse data from multiple sources (PayTabs can send via GET query params or POST body)
        payload = {}

        # First, try to get from query parameters (most common when PayTabs redirects)
        if request.GET:
            payload = dict(request.GET)
            # Convert list values to single values (Django QueryDict returns lists)
            for key, value in payload.items():
                if isinstance(value, list) and len(value) > 0:
                    payload[key] = value[0]

        # If tran_ref is missing, try to get it from the payment record using subscription_id
        tran_ref = None
        subscription_id_param = request.GET.get("subscription_id") or payload.get(
            "subscription_id"
        )
        if subscription_id_param:
            try:
                subscription_id = int(subscription_id_param)
                # Find the most recent payment for this subscription
                paytabs_gateway = PaymentGateway.objects.filter(
                    name__icontains="paytabs", enabled=True
                ).first()

                if paytabs_gateway:
                    payment = (
                        Payment.objects.filter(
                            subscription_id=subscription_id,
                            payment_method=paytabs_gateway,
                        )
                        .order_by("-created_at")
                        .first()
                    )

                    if payment and payment.tran_ref:
                        tran_ref = payment.tran_ref
                        logger.info(f"Found tran_ref from payment record: {tran_ref}")
            except (ValueError, Payment.DoesNotExist) as e:
                logger.warning(f"Could not find payment record: {str(e)}")

        if not tran_ref:
            logger.error(
                "Missing tran_ref in return URL - all data: %s",
                {
                    "GET": dict(request.GET),
                    "POST": dict(request.POST) if hasattr(request, "POST") else {},
                    "body": request_body_str,
                    "payload": payload,
                },
            )
            # Redirect to frontend with error status
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Missing transaction reference"
            )

        # Verify transaction (like uchat_paytabs_gateway)
        logger.info(f"Attempting to verify PayTabs payment with tran_ref: {tran_ref}")
        result = verify_paytabs_payment(tran_ref)
        logger.info(f"PayTabs verified result: {json.dumps(result, indent=2, default=str)}")

        # Extract subscription from cart_id
        cart_id = result.get("cart_id")
        if not cart_id:
            logger.error("Missing cart_id in verified result")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Invalid transaction"
            )

        subscription_id = int(cart_id.replace("SUB-", ""))
        logger.info(f"Extracted subscription_id from cart_id: {subscription_id}")
        subscription = Subscription.objects.get(id=subscription_id)
        logger.info(f"Found subscription: ID={subscription.id}, Company={subscription.company.name if subscription.company else 'None'}, "
                   f"Current end_date={subscription.end_date}, is_active={subscription.is_active}")

        # Get subscription_id from URL params if available (for redirect)
        url_subscription_id = request.GET.get("subscription_id") or payload.get(
            "subscription_id"
        )
        if url_subscription_id:
            try:
                subscription_id = int(url_subscription_id)
            except:
                pass

        # Update payment status (like uchat_paytabs_gateway)
        payment_result = result.get("payment_result", {})
        payment_status = payment_result.get("response_status")
        logger.info(f"Payment status from PayTabs: {payment_status}")
        logger.info(f"Full payment_result: {json.dumps(payment_result, indent=2, default=str)}")
        
        if payment_status == "A":  # Approved
            logger.info("Payment APPROVED - Processing payment and updating subscription...")
            # Update or create payment record first to get the amount
            paytabs_gateway = PaymentGateway.objects.filter(
                name__icontains="paytabs", enabled=True
            ).first()

            amount = float(result.get("cart_amount", 0))
            cart_currency = (result.get("cart_currency") or "IQD").upper()[:10]
            if not cart_currency:
                cart_currency = "IQD"
            
            try:
                from settings.models import SystemSettings
                system_settings = SystemSettings.get_settings()
                usd_to_iqd_rate = float(system_settings.usd_to_iqd_rate)
            except Exception:
                usd_to_iqd_rate = 1300.0
            from decimal import Decimal
            if cart_currency == "IQD" and usd_to_iqd_rate:
                exchange_rate = Decimal(str(usd_to_iqd_rate))
                amount_usd_val = Decimal(str(amount)) / exchange_rate
                amount_usd_val = amount_usd_val.quantize(Decimal("0.01"))
            else:
                exchange_rate = Decimal("1")
                amount_usd_val = Decimal(str(amount))
            
            # Find existing payment or create new one
            payment = None
            if paytabs_gateway:
                payment = (
                    Payment.objects.filter(
                        subscription=subscription, payment_method=paytabs_gateway
                    )
                    .order_by("-created_at")
                    .first()
                )

                if payment:
                    # Update existing payment with actual charged amount, currency, rate and amount_usd
                    logger.info(f"Updating existing payment: ID={payment.id}, Old status={payment.payment_status}, "
                               f"Old amount={payment.amount} {payment.currency}, Old tran_ref={payment.tran_ref}")
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = tran_ref
                    payment.amount = amount
                    payment.currency = cart_currency
                    payment.exchange_rate = exchange_rate
                    payment.amount_usd = amount_usd_val
                    payment.save()
                    logger.info(f"Payment updated successfully: ID={payment.id}, New status={payment.payment_status}, "
                               f"New amount={payment.amount} {payment.currency}, New tran_ref={payment.tran_ref}")
                else:
                    # Create new payment (PayTabs return sends amount in gateway currency, e.g. IQD)
                    logger.info(f"Creating new payment: subscription_id={subscription.id}, amount={amount} {cart_currency}, "
                               f"gateway={paytabs_gateway.name if paytabs_gateway else 'None'}, tran_ref={tran_ref}")
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        currency=cart_currency,
                        exchange_rate=exchange_rate,
                        amount_usd=amount_usd_val,
                        payment_method=paytabs_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=tran_ref,
                    )
                    logger.info(f"Payment created successfully: ID={payment.id}, status={payment.payment_status}")
            
            amount_usd = float(amount_usd_val)

            if payment:
                try:
                    finalize_completed_payment(subscription, payment, amount_usd)
                    subscription.refresh_from_db()
                except ValueError as err:
                    logger.error("Billing apply failed (PayTabs): %s", err, exc_info=True)
                    frontend_url = settings.FRONTEND_URL
                    return redirect(
                        f"{frontend_url}/payment/success?subscription_id={subscription_id}"
                        f"&status=failed&message={str(err)}"
                    )

        # Redirect to frontend success page
        frontend_url = settings.FRONTEND_URL
        if payment_status == "A":
            logger.info(f"Redirecting to success page: {frontend_url}/payment/success?subscription_id={subscription_id}&status=success&tranRef={tran_ref}")
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=success&tranRef={tran_ref}"
            )
        else:
            logger.warning(f"Payment NOT approved (status: {payment_status}). Redirecting to failed page.")
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Payment failed"
            )

    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"ERROR processing PayTabs return: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback:", exc_info=True)
        logger.error("=" * 80)
        frontend_url = settings.FRONTEND_URL
        subscription_id = request.GET.get("subscription_id") or ""
        return redirect(
            f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=error&message={str(e)}"
        )

