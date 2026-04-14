import json
import logging
import requests
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
    PaymentGateway,
    PaymentStatus,
    PaymentGatewayStatus,
    Invoice,
    InvoiceStatus,
)
from ..serializers import CreateZaincashPaymentSerializer
from ..zaincash_utils import verify_zaincash_payment, create_zaincash_payment_session
from ..services.billing import finalize_completed_payment, resolve_checkout_pricing
from ..phone_verification_gate import require_owner_phone_verified

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([AllowAny])
def create_zaincash_payment(request):
    """
    Create a Zain Cash payment session for a subscription
    POST /api/payments/create-zaincash-session/
    Body: { subscription_id: int }
    """
    # Validate serializer
    serializer = CreateZaincashPaymentSerializer(data=request.data)
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
        f"Creating Zain Cash payment for subscription {subscription_id}: "
        f"intent={intent}, plan={target_plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return error_response(
            "Plan is free, no payment required",
            code="bad_request",
        )

    # Prepare Zain Cash payment request
    user_email = subscription.company.owner.email
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username

    # Zain Cash redirects to FRONTEND URL with ?token=XXX
    # The frontend will then call the backend to verify the token
    return_url = f"{settings.FRONTEND_URL}/payment/success?subscription_id={subscription_id}"

    customer_phone = subscription.company.owner.phone or ""

    try:
        result = create_zaincash_payment_session(
            amount=amount,
            customer_email=user_email,
            customer_name=user_name,
            customer_phone=customer_phone,
            subscription_id=subscription_id,
            return_url=return_url,
        )

        # Log the response for debugging
        logger.info(f"Zain Cash API response: {result}")
        
        # Extract transaction ID and payment URL from result
        # The utility function now returns: {"id": "...", "payment_url": "..."}
        transaction_id = result.get("id") or result.get("transaction_id")
        payment_url = result.get("payment_url")
        
        if not transaction_id:
            logger.error(f"Zain Cash API did not return transaction ID. Response: {result}")
            return error_response(
                "Failed to create payment session: No transaction ID received",
                code="bad_request",
            )
        
        if not payment_url:
            # Construct payment URL if not provided
            environment = subscription.payment_method.config.get("environment", "test") if subscription.payment_method else "test"
            api_base_url = "https://api.zaincash.iq" if environment == "live" else "https://test.zaincash.iq"
            payment_url = f"{api_base_url}/transaction/pay?id={transaction_id}"
        
        # Get Zain Cash gateway using the utility function
        from ..zaincash_utils import get_zaincash_gateway
        zaincash_gateway = get_zaincash_gateway()

        if not zaincash_gateway:
            logger.error("Zain Cash gateway not found after payment session creation")
            return error_response(
                "Zain Cash gateway is not configured or enabled",
                code="bad_request",
            )
        
        # Create payment record (amount in USD at creation; return callback will update to IQD)
        from decimal import Decimal
        payment = Payment.objects.create(
            subscription=subscription,
            amount=amount,
            currency="USD",
            exchange_rate=Decimal("1"),
            amount_usd=Decimal(str(amount)),
            payment_method=zaincash_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref=transaction_id,  # Store transaction ID
            target_plan=target_plan,
            billing_cycle=billing_cycle,
        )

        logger.info(
            f"Created Zain Cash payment record: payment_id={payment.id}, "
            f"transaction_id={transaction_id}, subscription_id={subscription_id}, "
            f"payment_url={payment_url}"
        )

        # Return the payment URL that frontend should redirect user to
        return success_response(
            data={
                "redirect_url": payment_url,
                "transaction_id": transaction_id,
            },
        )
    except requests.exceptions.HTTPError as e:
        error_details = {}
        try:
            error_details = e.response.json()
        except:
            error_details = {"message": e.response.text}

        return error_response(
            f"Zain Cash API error: {error_details.get('message', str(e))}",
            code="bad_request",
            details=error_details,
        )
    except ValueError as e:
        # Handle configuration errors (gateway not found, credentials missing, etc.)
        logger.error(f"Zain Cash configuration error: {str(e)}")
        return error_response(str(e), code="bad_request")
    except requests.exceptions.RequestException as e:
        logger.error(f"Zain Cash request error: {str(e)}", exc_info=True)
        return error_response(
            f"Error communicating with Zain Cash: {str(e)}",
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
def zaincash_return(request):
    """
    Handle Zain Cash return URL - Zain Cash redirects here after payment
    Zain Cash sends token in query params or POST body
    This endpoint processes payment and redirects to frontend success page
    """
    logger = logging.getLogger(__name__)

    # Get subscription_id early for error handling
    subscription_id = None
    try:
        subscription_id_param = request.GET.get("subscription_id")
        if subscription_id_param:
            subscription_id = int(subscription_id_param)
    except (ValueError, TypeError):
        pass

    try:
        # Parse data from multiple sources
        payload = {}
        token = None

        # First, try to get from query parameters
        # Zain Cash typically sends token as 'token' or 'id' in query params
        if request.GET:
            payload = dict(request.GET)
            for key, value in payload.items():
                if isinstance(value, list) and len(value) > 0:
                    payload[key] = value[0]
            # Try multiple possible parameter names
            token = payload.get("token") or payload.get("id") or payload.get("transactionToken") or payload.get("jwt")

        # Check POST form data (form-encoded)
        if not token and request.method == "POST":
            if hasattr(request, "POST") and request.POST:
                token = request.POST.get("token") or request.POST.get("id") or request.POST.get("transactionToken") or request.POST.get("jwt")
                if token:
                    payload.update(dict(request.POST))

        # Also check POST body (JSON) - use request.data for DRF compatibility
        if not token and request.method == "POST":
            try:
                # Use request.data for JSON (DRF handles this safely)
                if hasattr(request, 'data') and request.data:
                    body_data = request.data
                    if isinstance(body_data, dict):
                        token = body_data.get("token") or body_data.get("id") or body_data.get("transactionToken") or body_data.get("jwt")
                        if token:
                            payload.update(body_data)
            except Exception:
                # Fallback: try to read body if not already consumed
                try:
                    if hasattr(request, '_body') and request._body:
                        body_data = json.loads(request._body.decode("utf-8"))
                        token = body_data.get("token") or body_data.get("id") or body_data.get("transactionToken") or body_data.get("jwt")
                        if token:
                            payload.update(body_data)
                except Exception:
                    pass
        
        # Also check URL fragment (after #) - some payment gateways use this
        if not token and request.META.get('HTTP_REFERER'):
            referer = request.META.get('HTTP_REFERER', '')
            if '#' in referer:
                fragment = referer.split('#')[1]
                # Try to extract token from fragment
                if 'token=' in fragment:
                    token = fragment.split('token=')[1].split('&')[0]
                elif 'id=' in fragment:
                    token = fragment.split('id=')[1].split('&')[0]
        
        # Log what we received for debugging
        if token:
            logger.info(f"Found token in request: {token[:30]}... (length: {len(token)})")
        else:
            # Safely get body info without accessing request.body directly
            body_info = None
            try:
                if hasattr(request, 'data') and request.data:
                    body_info = str(request.data)[:200]
                elif hasattr(request, '_body') and request._body:
                    body_info = request._body.decode('utf-8')[:200]
            except Exception:
                body_info = "Unable to read body"
            
            logger.warning(
                f"No token found in request. "
                f"Method: {request.method}, "
                f"GET params: {dict(request.GET)}, "
                f"POST: {dict(request.POST) if hasattr(request, 'POST') else {}}, "
                f"Body: {body_info}, "
                f"Referer: {request.META.get('HTTP_REFERER', 'N/A')[:200]}"
            )

        # If token is missing, try to get it from the payment record using subscription_id
        subscription_id_param = request.GET.get("subscription_id") or payload.get("subscription_id")
        if not token and subscription_id_param:
            try:
                subscription_id = int(subscription_id_param)
                # Use the same gateway lookup function
                from ..zaincash_utils import get_zaincash_gateway
                zaincash_gateway = get_zaincash_gateway()

                if zaincash_gateway:
                    payment = (
                        Payment.objects.filter(
                            subscription_id=subscription_id,
                            payment_method=zaincash_gateway,
                        )
                        .order_by("-created_at")
                        .first()
                    )

                    if payment and payment.tran_ref:
                        # Check if tran_ref is a valid JWT token (has 3 segments separated by dots)
                        # If it's just a transaction ID, we can't verify it as a JWT
                        tran_ref = payment.tran_ref
                        if tran_ref.count('.') == 2:
                            # It's a JWT token, use it for verification
                            token = tran_ref
                            logger.info(f"Using JWT token from payment record: {token[:20]}...")
                        else:
                            # It's just a transaction ID, not a JWT token
                            # Zain Cash should send the token in the redirect URL
                            logger.warning(f"Payment record has transaction ID but no JWT token. Transaction ID: {tran_ref}")
                            token = None
                    else:
                        logger.warning(f"No payment record found for subscription {subscription_id} with transaction ID")
            except (ValueError, Payment.DoesNotExist) as e:
                logger.warning(f"Could not find payment record: {str(e)}")
            except Exception as e:
                logger.error(f"Error retrieving payment record: {str(e)}", exc_info=True)

        # If no token, try to verify using payment record
        if not token:
            subscription_id_param = request.GET.get("subscription_id") or payload.get("subscription_id")
            if subscription_id_param:
                try:
                    subscription_id = int(subscription_id_param)
                    from ..zaincash_utils import get_zaincash_gateway
                    zaincash_gateway = get_zaincash_gateway()
                    
                    if zaincash_gateway:
                        payment = (
                            Payment.objects.filter(
                                subscription_id=subscription_id,
                                payment_method=zaincash_gateway,
                            )
                            .order_by("-created_at")
                            .first()
                        )
                        
                        if payment and payment.tran_ref:
                            # We have a payment record with transaction ID
                            # IMPORTANT: We should NOT automatically activate the subscription
                            # just because Zain Cash redirected here. The user might have cancelled
                            # or there might have been an error. We need the JWT token to verify.
                            
                            logger.warning(
                                f"Payment record found but no JWT token received from Zain Cash. "
                                f"Transaction ID: {payment.tran_ref}. "
                                f"Cannot verify payment without JWT token. "
                                f"Subscription will NOT be activated automatically."
                            )
                            
                            # Check if payment is still pending - if so, provide a message that payment is being processed
                            # The user should check their Zain Cash account or wait for webhook/confirmation
                            if payment.payment_status == PaymentStatus.PENDING.value:
                                frontend_url = settings.FRONTEND_URL
                                return redirect(
                                    f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=pending&message=Payment is being processed. Please wait a few moments and refresh, or contact support with transaction ID: {payment.tran_ref}"
                                )
                            else:
                                # Payment status is not pending, but we can't verify - show error
                                frontend_url = settings.FRONTEND_URL
                                return redirect(
                                    f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Payment verification failed. Please contact support with transaction ID: {payment.tran_ref}"
                                )
                except Exception as e:
                    logger.error(f"Error processing payment without token: {str(e)}", exc_info=True)
            
            # If we can't process without token, return error
            # Safely get body info
            body_info = None
            try:
                if hasattr(request, 'data') and request.data:
                    body_info = str(request.data)
                elif hasattr(request, '_body') and request._body:
                    body_info = request._body.decode("utf-8")
            except Exception:
                body_info = "Unable to read body"
            
            logger.error(
                "Missing token in return URL - all data: %s",
                {
                    "GET": dict(request.GET),
                    "POST": dict(request.POST) if hasattr(request, "POST") else {},
                    "body": body_info,
                    "payload": payload,
                },
            )
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Missing transaction token"
            )

        # Verify transaction by decoding JWT token
        result = verify_zaincash_payment(token)
        logger.info(f"Zain Cash verified result: {result}")

        # Check payment status first - Zain Cash returns status in the token
        payment_status = result.get("status")
        error_message = result.get("msg") or result.get("message")
        
        # Check if payment failed
        if payment_status == "failed":
            logger.warning(f"Zain Cash payment failed: {error_message}")
            # Get subscription_id from orderid (lowercase) or orderId (camelCase)
            order_id = result.get("orderid") or result.get("orderId")
            if order_id:
                try:
                    subscription_id = int(order_id.replace("SUB-", ""))
                except (ValueError, AttributeError):
                    subscription_id = None
            else:
                # Try to get from URL params
                subscription_id = request.GET.get("subscription_id") or payload.get("subscription_id")
                if subscription_id:
                    try:
                        subscription_id = int(subscription_id)
                    except (ValueError, TypeError):
                        subscription_id = None
            
            # Check if this is an API call (from frontend) or browser redirect
            # API calls have JSON body data, browser redirects come from Zain Cash with GET or form POST
            is_api_call = (
                request.method == 'POST' and 
                hasattr(request, 'data') and 
                request.data and 
                isinstance(request.data, dict) and
                ('token' in request.data or 'subscription_id' in request.data)
            )
            
            if is_api_call:
                # Return JSON response for API calls
                return error_response(
                    error_message or "Payment failed",
                    code="payment_failed",
                    details={
                        "gateway_status": "failed",
                        "subscription_id": subscription_id,
                    },
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            else:
                # Redirect for browser calls
                frontend_url = settings.FRONTEND_URL
                if subscription_id:
                    return redirect(
                        f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message={error_message or 'Payment failed'}"
                    )
                else:
                    return redirect(
                        f"{frontend_url}/payment/success?status=failed&message={error_message or 'Payment failed'}"
                    )

        # Extract subscription from orderid (lowercase) or orderId (camelCase)
        order_id = result.get("orderid") or result.get("orderId")
        if not order_id:
            logger.error(f"Missing orderId/orderid in verified result. Result keys: {result.keys()}")
            # Try to get subscription_id from URL params as fallback
            subscription_id = request.GET.get("subscription_id") or payload.get("subscription_id")
            if subscription_id:
                try:
                    subscription_id = int(subscription_id)
                except (ValueError, TypeError):
                    subscription_id = None
            
            # Check if this is an API call
            is_api_call = (
                request.method == 'POST' and 
                hasattr(request, 'data') and 
                request.data and 
                isinstance(request.data, dict) and
                ('token' in request.data or 'subscription_id' in request.data)
            )
            if is_api_call:
                return error_response(
                    "Invalid transaction: Missing order ID",
                    code="bad_request",
                    details={"gateway_status": "error"},
                )
            else:
                frontend_url = settings.FRONTEND_URL
                if subscription_id:
                    return redirect(
                        f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Invalid transaction"
                    )
                else:
                    return redirect(
                        f"{frontend_url}/payment/success?status=failed&message=Invalid transaction"
                    )

        subscription_id = int(order_id.replace("SUB-", ""))
        subscription = Subscription.objects.get(id=subscription_id)

        # Get subscription_id from URL params if available (override with URL param if provided)
        url_subscription_id = request.GET.get("subscription_id") or payload.get("subscription_id")
        if url_subscription_id:
            try:
                subscription_id = int(url_subscription_id)
                subscription = Subscription.objects.get(id=subscription_id)
            except (ValueError, Subscription.DoesNotExist):
                pass

        # Update payment status - check if payment is successful
        if payment_status == "success" or result.get("id"):  # Payment successful
            from ..zaincash_utils import get_zaincash_gateway
            zaincash_gateway = get_zaincash_gateway()

            # Zain Cash charges in IQD; decoded token contains the amount actually charged (IQD)
            amount_iqd = float(result.get("amount", 0))
            payment_currency = "IQD"
            try:
                from settings.models import SystemSettings
                system_settings = SystemSettings.get_settings()
                usd_to_iqd_rate = float(system_settings.usd_to_iqd_rate)
            except Exception:
                usd_to_iqd_rate = 1300.0
            from decimal import Decimal
            exchange_rate = Decimal(str(usd_to_iqd_rate))
            amount_usd_val = (Decimal(str(amount_iqd)) / exchange_rate).quantize(Decimal("0.01"))

            # Find existing payment first (it should exist from when we created the session)
            payment = None
            amount = amount_iqd
            if zaincash_gateway:
                payment = (
                    Payment.objects.filter(
                        subscription=subscription, payment_method=zaincash_gateway
                    )
                    .order_by("-created_at")
                    .first()
                )

                if payment:
                    # Update existing payment with actual charged amount, currency, rate and amount_usd
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = token  # Store the JWT token from Zain Cash
                    payment.amount = amount
                    payment.currency = payment_currency
                    payment.exchange_rate = exchange_rate
                    payment.amount_usd = amount_usd_val
                    payment.save()
                else:
                    # Payment record doesn't exist - use amount from token (IQD)
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        currency=payment_currency,
                        exchange_rate=exchange_rate,
                        amount_usd=amount_usd_val,
                        payment_method=zaincash_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=token,
                    )
                    logger.warning(
                        f"Created new payment record for subscription {subscription_id} - "
                        f"this shouldn't normally happen as payment should exist from session creation"
                    )
            
            amount_usd = float(amount_usd_val)
            if payment:
                try:
                    finalize_completed_payment(subscription, payment, amount_usd)
                    subscription.refresh_from_db()
                except ValueError as err:
                    logger.error("Billing apply failed (ZainCash): %s", err, exc_info=True)
                    is_api_call = (
                        request.method == "POST"
                        and hasattr(request, "data")
                        and request.data
                        and isinstance(request.data, dict)
                        and ("token" in request.data or "subscription_id" in request.data)
                    )
                    if is_api_call:
                        return error_response(str(err), code="billing_error")
                    frontend_url = settings.FRONTEND_URL
                    return redirect(
                        f"{frontend_url}/payment/success?subscription_id={subscription_id}"
                        f"&status=failed&message={str(err)}"
                    )

            due = subscription.end_date.date() if subscription.end_date else timezone.now().date()
            Invoice.objects.create(
                subscription=subscription,
                amount=amount_usd,
                due_date=due,
                status=InvoiceStatus.PAID.value,
            )

            logger.info(
                "Zain Cash payment completed for subscription %s, amount: %s %s (≈%s USD), billing_cycle: %s",
                subscription_id,
                amount,
                payment_currency,
                amount_usd,
                (payment.billing_cycle if payment else None) or subscription.billing_cycle,
            )

            # Check if this is an API call (from frontend) or browser redirect
            is_api_call = (
                request.method == 'POST' and 
                hasattr(request, 'data') and 
                request.data and 
                isinstance(request.data, dict) and
                ('token' in request.data or 'subscription_id' in request.data)
            )
            
            if is_api_call:
                # Return JSON response for API calls
                return success_response(
                    data={
                        "status": "success",
                        "message": "Payment completed successfully",
                        "subscription_id": subscription_id,
                        "subscription_active": subscription.is_active,
                    },
                )
            else:
                # Redirect for browser calls
                frontend_url = settings.FRONTEND_URL
                return redirect(
                    f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=success"
                )
        else:
            # Payment status is not success and no ID - this shouldn't happen but handle it
            logger.warning(f"Zain Cash payment status unknown for subscription {subscription_id}: {payment_status}")
            is_api_call = request.headers.get('Content-Type') == 'application/json' or (
                request.method == 'POST' and hasattr(request, 'data') and request.data
            )
            
            if is_api_call:
                return error_response(
                    "Payment status unknown",
                    code="bad_request",
                    details={
                        "gateway_status": "error",
                        "subscription_id": subscription_id,
                    },
                )
            else:
                frontend_url = settings.FRONTEND_URL
                return redirect(
                    f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Payment status unknown"
                )

    except Subscription.DoesNotExist:
        logger.error(f"Subscription not found in Zain Cash return handler")
        frontend_url = settings.FRONTEND_URL
        return redirect(
            f"{frontend_url}/payment/success?status=failed&message=Subscription not found"
        )
    except Exception as e:
        logger.error(f"Error processing Zain Cash return: {str(e)}", exc_info=True)
        frontend_url = settings.FRONTEND_URL
        # Get subscription_id from request if not already set
        if not subscription_id:
            try:
                subscription_id_param = request.GET.get("subscription_id")
                if subscription_id_param:
                    subscription_id = int(subscription_id_param)
            except (ValueError, TypeError):
                pass
        
        # Build redirect URL safely
        if subscription_id:
            redirect_url = f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=error&message={str(e)}"
        else:
            redirect_url = f"{frontend_url}/payment/success?status=error&message={str(e)}"
        
        return redirect(redirect_url)
