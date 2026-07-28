import json
import logging
import requests
from decimal import Decimal

from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response, validation_error_response

from ..models import (
    Plan,
    Subscription,
    Payment,
    PaymentStatus,
    PaymentGateway,
)
from ..serializers import CreateQicardPaymentSerializer
from ..qicard_utils import (
    verify_qicard_payment,
    create_qicard_payment_session,
    get_qicard_gateway,
)
from ..services.billing import finalize_completed_payment, resolve_checkout_pricing
from ..services.checkout_auth import require_subscription_owner
from ..services.payment_completion import (
    attach_checkout_session,
    confirm_and_finalize_payment,
    find_payment_by_tran_ref,
    find_reusable_pending_payment,
)
from ..phone_verification_gate import require_owner_phone_verified

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_qicard_payment(request):
    """
    Create a QiCard payment session for a subscription
    POST /api/payments/create-qicard-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: 'monthly' | 'yearly' }
    """
    # Validate serializer
    serializer = CreateQicardPaymentSerializer(data=request.data)
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

    amount = float(amount_dec)
    logger.info(
        "Creating QiCard payment for subscription %s: intent=%s plan=%s cycle=%s amount=%s",
        subscription_id,
        intent,
        target_plan.name,
        billing_cycle,
        amount,
    )

    if amount <= 0:
        return error_response(
            "Plan is free, no payment required",
            code="bad_request",
        )

    qicard_gateway = get_qicard_gateway()
    if not qicard_gateway:
        return error_response(
            "QiCard gateway is not configured or enabled",
            code="bad_request",
        )

    reusable = find_reusable_pending_payment(
        subscription=subscription,
        gateway=qicard_gateway,
        target_plan=target_plan,
        billing_cycle=billing_cycle,
        amount_usd=amount_dec,
    )
    if reusable and reusable.checkout_url:
        return success_response(
            data={
                "redirect_url": reusable.checkout_url,
                "payment_id": reusable.tran_ref,
                "request_id": (reusable.session_meta or {}).get("request_id"),
            },
        )

    user_email = subscription.company.owner.email
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username

    return_url = f"{settings.API_BASE_URL}/api/payments/qicard-return/?subscription_id={subscription_id}"
    notification_url = f"{settings.API_BASE_URL}/api/payments/qicard-webhook/"

    customer_phone = subscription.company.owner.phone or ""

    try:
        result = create_qicard_payment_session(
            amount=amount,
            customer_email=user_email,
            customer_name=user_name,
            customer_phone=customer_phone,
            subscription_id=subscription_id,
            return_url=return_url,
            notification_url=notification_url,
        )

        payment_id = result.get("payment_id")
        form_url = result.get("form_url")
        request_id = result.get("request_id")
        
        if not payment_id or not form_url:
            logger.error("QiCard API did not return payment ID or form URL")
            return error_response(
                "Failed to create payment session: No payment ID or form URL received",
                code="bad_request",
            )
        
        payment = Payment.objects.create(
            subscription=subscription,
            amount=amount,
            currency="USD",
            exchange_rate=Decimal("1"),
            amount_usd=Decimal(str(amount)),
            payment_method=qicard_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref=payment_id,
            target_plan=target_plan,
            billing_cycle=billing_cycle,
        )
        attach_checkout_session(
            payment,
            tran_ref=payment_id,
            checkout_url=form_url,
            session_meta={"request_id": request_id},
        )

        logger.info(
            "Created QiCard payment record: payment_id=%s qicard_payment_id=%s subscription_id=%s",
            payment.id,
            payment_id,
            subscription_id,
        )

        return success_response(
            data={
                "redirect_url": form_url,
                "payment_id": payment_id,
                "request_id": request_id,
            },
        )
    except requests.exceptions.HTTPError as e:
        error_details = {}
        try:
            error_details = e.response.json()
        except Exception:
            error_details = {"message": e.response.text}

        return error_response(
            f"QiCard API error: {error_details.get('message', str(e))}",
            code="bad_request",
            details=error_details,
        )
    except ValueError as e:
        logger.error("QiCard configuration error: %s", e)
        return error_response(str(e), code="bad_request")
    except requests.exceptions.RequestException as e:
        logger.error("QiCard request error: %s", e, exc_info=True)
        return error_response(
            f"Error communicating with QiCard: {str(e)}",
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
def qicard_return(request):
    """
    Handle QiCard return URL - QiCard redirects here after payment
    This endpoint verifies payment status and redirects to frontend success/failure page
    """
    logger = logging.getLogger(__name__)

    logger.info("=" * 80)
    logger.info("QICARD RETURN URL CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"GET params: {dict(request.GET)}")
    logger.info(f"POST data: {dict(request.POST) if hasattr(request, 'POST') else 'N/A'}")
    logger.info(f"Request data: {getattr(request, 'data', 'N/A')}")
    logger.info(f"Request body: {request.body.decode('utf-8') if request.body else 'Empty'}")
    logger.info("=" * 80)

    subscription_id = None
    payment_id = None
    status_param = None

    try:
        # QiCard redirects with paymentId and status in query parameters
        payment_id = request.GET.get("paymentId")
        status_param = request.GET.get("status")
        subscription_id_param = request.GET.get("subscription_id")

        if subscription_id_param:
            try:
                subscription_id = int(subscription_id_param)
            except (ValueError, TypeError):
                subscription_id = None

        if not payment_id:
            logger.error("Missing paymentId in QiCard return URL")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Missing payment ID"
            )

        # Verify payment status with QiCard API
        logger.info(f"Attempting to verify QiCard payment with paymentId: {payment_id}")
        from ..qicard_utils import verify_qicard_payment, get_qicard_gateway
        verification_result = verify_qicard_payment(payment_id)
        logger.info(f"QiCard verification result: {json.dumps(verification_result, indent=2, default=str)}")

        qicard_payment_status = verification_result.get("status")
        logger.info(f"QiCard payment status from API: {qicard_payment_status}")

        if qicard_payment_status == "SUCCESS":
            logger.info("QiCard payment SUCCESS - Processing payment and updating subscription...")
            
            qicard_gateway = get_qicard_gateway()
            if not qicard_gateway:
                logger.error("QiCard gateway not found during return processing")
                frontend_url = settings.FRONTEND_URL
                return redirect(
                    f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=error&message=Payment gateway not configured"
                )

            # Note: verification_result.get("amount") returns amount in IQD
            # We should NOT update payment.amount with this value
            # The payment.amount was already set correctly (in USD) when payment was created
            
            # Find payment record - try with subscription_id first, then without
            payment = None
            if subscription_id:
                payment = Payment.objects.filter(
                    subscription__id=subscription_id,
                    payment_method=qicard_gateway,
                    tran_ref=payment_id,
                ).order_by("-created_at").first()
            
            # If not found, try without subscription_id filter
            if not payment:
                payment = Payment.objects.filter(
                    payment_method=qicard_gateway,
                    tran_ref=payment_id,
                ).order_by("-created_at").first()
                if payment:
                    subscription_id = payment.subscription.id
                    logger.info(f"Found payment without subscription_id filter, subscription_id={subscription_id}")

            if not payment:
                # If payment record not found, try to create one
                logger.warning(f"Payment record not found for QiCard paymentId {payment_id}. Attempting to create new record.")
                if not subscription_id:
                    # Try to get subscription_id from additionalInfo if not in URL
                    additional_info = verification_result.get("additionalInfo", {})
                    sub_id_from_info = additional_info.get("subscription_id")
                    if sub_id_from_info:
                        try:
                            subscription_id = int(sub_id_from_info)
                        except (ValueError, TypeError):
                            pass
                
                if subscription_id:
                    try:
                        subscription = Subscription.objects.get(id=subscription_id)
                        plan = subscription.plan
                        # Get original amount from plan (USD)
                        if plan:
                            # Determine billing cycle to get correct amount
                            days_diff = (subscription.end_date - subscription.start_date).days if subscription.end_date and subscription.start_date else 0
                            billing_cycle = "yearly" if days_diff >= 330 else "monthly"
                            original_amount = float(plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly)
                        else:
                            # Fallback: convert IQD back to USD (not ideal but better than storing IQD)
                            amount_iqd = float(verification_result.get("amount", 0))
                            try:
                                from settings.models import SystemSettings
                                system_settings = SystemSettings.get_settings()
                                USD_TO_IQD_RATE = float(system_settings.usd_to_iqd_rate)
                                original_amount = amount_iqd / USD_TO_IQD_RATE
                            except:
                                original_amount = amount_iqd / 1300  # Default rate
                        
                        from decimal import Decimal
                        payment = Payment.objects.create(
                            subscription=subscription,
                            amount=original_amount,  # Store original amount in USD
                            currency="USD",
                            exchange_rate=Decimal("1"),
                            amount_usd=Decimal(str(original_amount)),
                            payment_method=qicard_gateway,
                            payment_status=PaymentStatus.COMPLETED.value,
                            tran_ref=payment_id,
                        )
                        logger.info(f"Created new payment record for QiCard paymentId {payment_id}, payment.id={payment.id}, amount={original_amount} USD")
                    except Subscription.DoesNotExist:
                        logger.error(f"Subscription {subscription_id} not found for QiCard paymentId {payment_id}")
                        frontend_url = settings.FRONTEND_URL
                        return redirect(
                            f"{frontend_url}/payment/success?status=failed&message=Subscription not found for payment"
                        )
                else:
                    logger.error(f"Could not determine subscription_id for QiCard paymentId {payment_id}")
                    frontend_url = settings.FRONTEND_URL
                    return redirect(
                        f"{frontend_url}/payment/success?status=failed&message=Could not link payment to subscription"
                    )
            else:
                # Update existing payment - keep original amount (USD), only update status
                logger.info(f"Updating existing payment: ID={payment.id}, Old status={payment.payment_status}, "
                           f"Amount={payment.amount} USD (keeping original amount, not updating with IQD value)")
                payment.payment_status = PaymentStatus.COMPLETED.value
                # DO NOT update payment.amount - it's already in USD, verification_result.amount is in IQD
                payment.save(update_fields=['payment_status', 'updated_at'])
                logger.info(f"Updated payment {payment.id} to COMPLETED (amount remains {payment.amount} USD).")
            
            subscription = payment.subscription
            if subscription.plan:
                pay_usd = float(payment.amount_usd) if payment.amount_usd is not None else float(payment.amount)
                try:
                    finalize_completed_payment(subscription, payment, pay_usd)
                    subscription.refresh_from_db()
                except ValueError as err:
                    logger.error("Billing apply failed (QiCard): %s", err, exc_info=True)
                    frontend_url = settings.FRONTEND_URL
                    return redirect(
                        f"{frontend_url}/payment/success?subscription_id={subscription.id}"
                        f"&status=failed&message={str(err)}"
                    )
            else:
                logger.warning(f"Subscription {subscription.id} has no plan, cannot set end date.")

            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription.id}&status=success"
            )
        elif qicard_payment_status == "FAILED" or qicard_payment_status == "AUTHENTICATION_FAILED":
            logger.warning(f"QiCard payment FAILED (status: {qicard_payment_status}). Redirecting to failed page.")
            
            # Update payment status if payment record exists
            qicard_gateway = get_qicard_gateway()
            if qicard_gateway and payment_id:
                payment = Payment.objects.filter(
                    tran_ref=payment_id,
                    payment_method=qicard_gateway,
                ).order_by("-created_at").first()
                if payment:
                    payment.payment_status = PaymentStatus.FAILED.value
                    payment.save(update_fields=['payment_status', 'updated_at'])
                    logger.info(f"Updated payment {payment.id} to FAILED.")
            
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=failed&message=Payment failed"
            )
        else:
            logger.warning(f"QiCard payment status is {qicard_payment_status}. Redirecting to pending/failed page.")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=pending&message=Payment status is {qicard_payment_status}"
            )

    except Subscription.DoesNotExist:
        logger.error(f"Subscription not found in QiCard return handler for subscription_id={subscription_id}")
        frontend_url = settings.FRONTEND_URL
        return redirect(
            f"{frontend_url}/payment/success?status=failed&message=Subscription not found"
        )
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"ERROR processing QiCard return: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback:", exc_info=True)
        logger.error("=" * 80)
        frontend_url = settings.FRONTEND_URL
        return redirect(
            f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=error&message={str(e)}"
        )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def qicard_webhook(request):
    """
    Handle QiCard webhook notifications.
    Payload is a hint only — status is confirmed via QiCard API before finalize.
    POST /api/payments/qicard-webhook/
    """
    logger.info("QiCard webhook called method=%s", request.method)

    try:
        if hasattr(request, "data") and request.data:
            payload = request.data
        else:
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                payload = {}

        payment_id = payload.get("paymentId")
        if not payment_id:
            logger.error("Missing paymentId in QiCard webhook")
            return error_response(
                "Missing paymentId",
                code="bad_request",
            )

        payment = find_payment_by_tran_ref(str(payment_id))
        if not payment:
            logger.warning("Payment not found for QiCard payment_id: %s", payment_id)
            return error_response(
                "Payment not found",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        ok, reason = confirm_and_finalize_payment(payment, mark_failed=True)
        logger.info("QiCard webhook payment=%s ok=%s reason=%s", payment.id, ok, reason)
        return success_response(message="OK", data={"ok": ok, "reason": reason})

    except Exception as e:
        logger.error("ERROR processing QiCard webhook: %s", e, exc_info=True)
        return error_response(
            str(e),
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
