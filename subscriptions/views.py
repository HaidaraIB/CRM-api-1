from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import generics
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import redirect
from datetime import timedelta
import json
import requests
import logging

logger = logging.getLogger(__name__)

from .models import (
    Plan,
    Subscription,
    Payment,
    Invoice,
    Broadcast,
    PaymentGateway,
    BroadcastStatus,
    InvoiceStatus,
    PaymentStatus,
    PaymentGatewayStatus,
)
from .serializers import (
    PlanSerializer,
    PlanListSerializer,
    SubscriptionSerializer,
    SubscriptionListSerializer,
    PaymentSerializer,
    PaymentListSerializer,
    InvoiceSerializer,
    InvoiceListSerializer,
    BroadcastSerializer,
    BroadcastListSerializer,
    PaymentGatewaySerializer,
    PaymentGatewayListSerializer,
    CreatePaytabsPaymentSerializer,
)
from accounts.permissions import IsSuperAdmin
from .utils import send_broadcast_email
from .paytabs_utils import verify_paytabs_payment, create_paytabs_payment_session


class PlanViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Plan instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Plan.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "name_ar", "description", "description_ar"]
    ordering_fields = ["created_at", "price_monthly", "price_yearly"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return PlanListSerializer
        return PlanSerializer


class PublicPlanListView(generics.ListAPIView):
    """
    Public endpoint to list visible plans for onboarding/registration.
    """

    permission_classes = [AllowAny]
    serializer_class = PlanListSerializer
    pagination_class = None

    def get_queryset(self):
        return Plan.objects.filter(visible=True).order_by("price_monthly")


class SubscriptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Subscription instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage subscriptions
    """

    queryset = Subscription.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["company__name", "plan__name"]
    ordering_fields = ["created_at", "start_date", "end_date"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return SubscriptionListSerializer
        return SubscriptionSerializer


class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Payment instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage payments
    """

    queryset = Payment.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["payment_status", "payment_method", "subscription__company__name"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return PaymentListSerializer
        return PaymentSerializer


class InvoiceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Invoice instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage invoices
    """

    queryset = Invoice.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["invoice_number", "subscription__company__name", "status"]
    ordering_fields = ["created_at", "due_date", "amount"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return InvoiceListSerializer
        return InvoiceSerializer

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        """Mark an invoice as paid"""
        invoice = self.get_object()
        invoice.status = InvoiceStatus.PAID.value
        invoice.save()
        return Response({"status": "Invoice marked as paid"})


class BroadcastViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Broadcast instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Broadcast.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["subject", "content", "target", "status"]
    ordering_fields = ["created_at", "scheduled_at", "sent_at"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return BroadcastListSerializer
        return BroadcastSerializer

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Send a broadcast immediately"""
        broadcast = self.get_object()
        if broadcast.status == "sent":
            return Response(
                {"error": "Broadcast already sent"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Send email via SMTP
        result = send_broadcast_email(broadcast)

        if not result["success"]:
            return Response(
                {"error": result.get("error", "Failed to send broadcast")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update broadcast status
        broadcast.status = BroadcastStatus.SENT.value
        broadcast.sent_at = timezone.now()
        broadcast.save()

        return Response(
            {
                "status": "Broadcast sent successfully",
                "recipients_count": result.get("recipients_count", 0),
            }
        )

    @action(detail=True, methods=["post"])
    def schedule(self, request, pk=None):
        """Schedule a broadcast for later"""
        broadcast = self.get_object()
        scheduled_at = request.data.get("scheduled_at")
        if not scheduled_at:
            return Response(
                {"error": "scheduled_at is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        broadcast.status = BroadcastStatus.PENDING.value
        broadcast.scheduled_at = scheduled_at
        broadcast.save()
        return Response({"status": "Broadcast scheduled successfully"})


class PaymentGatewayViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing PaymentGateway instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = PaymentGateway.objects.all()
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description", "status"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_serializer_class(self):
        if self.action == "list":
            return PaymentGatewayListSerializer
        return PaymentGatewaySerializer

    @action(detail=True, methods=["post"])
    def toggle_enabled(self, request, pk=None):
        """Toggle gateway enabled status"""
        gateway = self.get_object()
        if gateway.status == PaymentGatewayStatus.SETUP_REQUIRED.value:
            return Response(
                {"error": "Gateway setup required before enabling"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gateway.enabled = not gateway.enabled
        gateway.status = (
            PaymentGatewayStatus.ACTIVE.value
            if gateway.enabled
            else PaymentGatewayStatus.DISABLED.value
        )
        gateway.save()
        return Response(
            {
                "status": f'Gateway {"enabled" if gateway.enabled else "disabled"}',
                "enabled": gateway.enabled,
            }
        )


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
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    subscription_id = serializer.validated_data.get("subscription_id")
    plan_id = serializer.validated_data.get("plan_id")
    billing_cycle_param = serializer.validated_data.get("billing_cycle")
    
    if not subscription_id:
        return Response(
            {"error": "subscription_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        subscription = Subscription.objects.get(id=subscription_id)
    except Subscription.DoesNotExist:
        return Response(
            {"error": "Subscription not found"}, status=status.HTTP_404_NOT_FOUND
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
            return Response(
                {"error": "Plan not found"}, status=status.HTTP_404_NOT_FOUND
            )

    # Allow renewals and plan changes even if subscription is active
    # Only block if subscription is active AND no plan_id or billing_cycle is provided (no action requested)
    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return Response(
            {"error": "Subscription is already active. Use renewal or plan change to proceed."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    plan = subscription.plan

    # Determine billing cycle:
    # 1. Use provided billing_cycle if available (from frontend selection)
    # 2. Otherwise, calculate from existing subscription end_date
    if billing_cycle_param:
        billing_cycle = billing_cycle_param
        logger.info(
            f"Using provided billing_cycle: {billing_cycle} for subscription {subscription_id}"
        )
    else:
        # Determine billing cycle based on end_date and start_date
        # This should match what was set during registration
        days_diff = (subscription.end_date - subscription.start_date).days
        # Use 330 days as threshold to account for slight variations
        billing_cycle = "yearly" if days_diff >= 330 else "monthly"
        logger.info(
            f"Calculated billing_cycle from subscription: {billing_cycle} "
            f"(days_diff={days_diff}) for subscription {subscription_id}"
        )
    
    # Get the correct amount based on billing cycle
    amount = float(
        plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly
    )
    
    # Log for debugging
    logger.info(
        f"Creating payment for subscription {subscription_id}: "
        f"plan={plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return Response(
            {"error": "Plan is free, no payment required"},
            status=status.HTTP_400_BAD_REQUEST,
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
                return Response(
                    {"error": "Paytabs gateway is not configured or enabled"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Create payment record
            tran_ref = result.get("tran_ref", "")
            payment = Payment.objects.create(
                subscription=subscription,
                amount=amount,
                payment_method=paytabs_gateway,  # Use ForeignKey
                payment_status=PaymentStatus.PENDING.value,
                tran_ref=tran_ref,  # Store tran_ref
            )

            return Response(
                {
                    "payment_id": payment.id,
                    "redirect_url": result.get("redirect_url"),
                    "tran_ref": tran_ref,
                }
            )
        else:
            # Error response from Paytabs
            error_msg = (
                result.get("message")
                or result.get("error")
                or "Failed to create payment session"
            )
            return Response(
                {"error": error_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
    except requests.exceptions.HTTPError as e:
        # Try to get error details from response
        error_details = {}
        try:
            error_details = e.response.json()
        except:
            error_details = {"message": e.response.text}

        return Response(
            {
                "error": f"Paytabs API error: {error_details.get('message', str(e))}",
                "details": error_details,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except requests.exceptions.RequestException as e:
        return Response(
            {"error": f"Error communicating with Paytabs: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except Exception as e:
        return Response(
            {"error": f"Unexpected error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
                    "body": request.body.decode("utf-8") if request.body else None,
                    "payload": payload,
                },
            )
            # Redirect to frontend with error status
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Missing transaction reference"
            )

        # Verify transaction (like uchat_paytabs_gateway)
        result = verify_paytabs_payment(tran_ref)
        logger.info(f"PayTabs verified result: {result}")

        # Extract subscription from cart_id
        cart_id = result.get("cart_id")
        if not cart_id:
            logger.error("Missing cart_id in verified result")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Invalid transaction"
            )

        subscription_id = int(cart_id.replace("SUB-", ""))
        subscription = Subscription.objects.get(id=subscription_id)

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
        payment_status = result.get("payment_result", {}).get("response_status")
        if payment_status == "A":  # Approved
            # Update or create payment record first to get the amount
            paytabs_gateway = PaymentGateway.objects.filter(
                name__icontains="paytabs", enabled=True
            ).first()

            amount = float(result.get("cart_amount", 0))
            
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
                    # Update existing payment
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = tran_ref
                    payment.amount = amount
                    payment.save()
                else:
                    # Create new payment
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        payment_method=paytabs_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=tran_ref,
                    )
            
            # Determine billing cycle based on payment amount
            plan = subscription.plan
            billing_cycle = None
            
            # Check if amount matches yearly price (with small tolerance for rounding)
            if abs(amount - float(plan.price_yearly)) < 0.01:
                billing_cycle = "yearly"
            # Check if amount matches monthly price (with small tolerance for rounding)
            elif abs(amount - float(plan.price_monthly)) < 0.01:
                billing_cycle = "monthly"
            else:
                # If amount doesn't match exactly, determine by comparing which is closer
                yearly_diff = abs(amount - float(plan.price_yearly))
                monthly_diff = abs(amount - float(plan.price_monthly))
                billing_cycle = "yearly" if yearly_diff < monthly_diff else "monthly"
                logger.warning(
                    f"Payment amount {amount} doesn't exactly match plan prices "
                    f"(yearly: {plan.price_yearly}, monthly: {plan.price_monthly}). "
                    f"Using {billing_cycle} based on closest match."
                )
            
            # Calculate correct end_date based on billing cycle
            # Rule: If end_date is in the future, add billing_cycle to it
            #       If end_date is in the past or present, add billing_cycle to now
            now = timezone.now()
            
            if subscription.end_date > now:
                # end_date is in the future: extend from end_date
                base_date = subscription.end_date
                logger.info(
                    f"Subscription {subscription.id} end_date is in the future ({subscription.end_date.strftime('%Y-%m-%d %H:%M:%S')}): extending from end_date"
                )
            else:
                # end_date is in the past or present: extend from now
                base_date = now
                logger.info(
                    f"Subscription {subscription.id} end_date is in the past/present ({subscription.end_date.strftime('%Y-%m-%d %H:%M:%S')}): extending from now ({base_date.strftime('%Y-%m-%d %H:%M:%S')})"
                )
            
            if billing_cycle == "yearly":
                new_end_date = base_date + timedelta(days=365)
            else:
                new_end_date = base_date + timedelta(days=30)
            
            # Update subscription with correct end_date and activate it
            subscription.is_active = True
            subscription.end_date = new_end_date
            subscription.save(update_fields=['is_active', 'end_date', 'updated_at'])
            
            logger.info(
                f"Activated subscription {subscription.id} for company {subscription.company.name}. "
                f"Billing cycle: {billing_cycle}, Amount: {amount}, "
                f"End date set to: {new_end_date.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # Mark company registration as completed
            company = subscription.company
            if company:
                company.registration_completed = True
                company.registration_completed_at = timezone.now()
                company.save()

        # Redirect to frontend success page
        frontend_url = settings.FRONTEND_URL
        if payment_status == "A":
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=success&tranRef={tran_ref}"
            )
        else:
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Payment failed"
            )

    except Exception as e:
        logger.error(f"Error processing return: {str(e)}", exc_info=True)
        frontend_url = settings.FRONTEND_URL
        subscription_id = request.GET.get("subscription_id") or ""
        return redirect(
            f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=error&message={str(e)}"
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def check_payment_status(request, subscription_id):
    """
    Check payment status by subscription_id - for frontend polling
    GET /api/payments/subscription/{subscription_id}/status/
    Returns payment status and subscription status
    """
    logger = logging.getLogger(__name__)
    try:
        subscription = Subscription.objects.get(id=subscription_id)
        logger.info(
            f"Checking payment status for subscription {subscription_id}, is_active: {subscription.is_active}"
        )

        paytabs_gateway = PaymentGateway.objects.filter(
            name__icontains="paytabs", enabled=True
        ).first()
        if not paytabs_gateway:
            logger.error("Paytabs gateway not found")
            return Response(
                {"error": "Paytabs gateway not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Get payment - handle case where payment might not exist yet
        try:
            payment = (
                Payment.objects.filter(
                    subscription=subscription, payment_method=paytabs_gateway
                )
                .order_by("-created_at")
                .first()
            )
        except Exception as e:
            logger.warning(f"Error fetching payment: {str(e)}")
            payment = None

        # Refresh subscription from DB to get latest is_active status
        subscription.refresh_from_db()

        # Get payment status from DB
        payment_status_value = payment.payment_status if payment else "pending"
        paytabs_status = None

        # PRIORITY: If subscription is active, payment MUST be completed
        # This is the source of truth - if subscription is active, payment is done
        if subscription.is_active:
            payment_status_value = PaymentStatus.COMPLETED.value
            paytabs_status = "A"
            logger.info(
                f"Subscription {subscription_id} is ACTIVE - returning completed status immediately"
            )
        elif payment:
            # If payment exists and has tran_ref, try to get paytabs_status
            if payment.tran_ref:
                try:
                    result = verify_paytabs_payment(payment.tran_ref)
                    paytabs_status = result.get("payment_result", {}).get(
                        "response_status"
                    )
                except Exception as e:
                    logger.warning(f"Could not verify payment with PayTabs: {str(e)}")
                    # If payment is completed in DB but verification fails, assume approved
                    if payment.payment_status == PaymentStatus.COMPLETED.value:
                        paytabs_status = "A"

            # If payment is completed in DB, ensure paytabs_status is set
            if (
                payment.payment_status == PaymentStatus.COMPLETED.value
                and not paytabs_status
            ):
                paytabs_status = "A"

        # Return all fields the frontend needs - ensure values match what frontend expects
        response_data = {
            "subscription_id": subscription_id,
            "subscription_active": bool(
                subscription.is_active
            ),  # Ensure it's a boolean
            "payment_status": payment_status_value,  # Should be "completed" if done
            "paytabs_status": paytabs_status,  # Should be "A" if approved
        }

        logger.info(
            f"Payment status check for subscription {subscription_id}: subscription.is_active={subscription.is_active}, payment_status={payment_status_value}, paytabs_status={paytabs_status}, payment exists={payment is not None}"
        )
        logger.info(f"Returning response: {response_data}")

        return Response(response_data, status=status.HTTP_200_OK)
    except Payment.DoesNotExist:
        return Response(
            {"error": "Payment not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Subscription.DoesNotExist:
        return Response(
            {"error": "Subscription not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error checking payment status: {str(e)}", exc_info=True)
        return Response(
            {"error": f"Error checking payment status: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
