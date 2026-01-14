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
    CreateZaincashPaymentSerializer,
    CreateStripePaymentSerializer,
    CreateQicardPaymentSerializer,
)
from accounts.permissions import IsSuperAdmin
from .utils import send_broadcast_email
from .paytabs_utils import verify_paytabs_payment, create_paytabs_payment_session
from .zaincash_utils import verify_zaincash_payment, create_zaincash_payment_session, test_zaincash_credentials
from .stripe_utils import verify_stripe_payment, create_stripe_payment_session, test_stripe_credentials
from .qicard_utils import verify_qicard_payment, create_qicard_payment_session, test_qicard_credentials


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


class PublicPaymentGatewayListView(generics.ListAPIView):
    """
    Public endpoint to list available payment gateways.
    Returns only active and enabled gateways for payment selection.
    No authentication required.
    """
    serializer_class = PaymentGatewayListSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    
    def get_queryset(self):
        return PaymentGateway.objects.filter(
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True
        ).order_by('name')


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
        from subscriptions.utils import send_broadcast_email, send_broadcast_push_notification
        from subscriptions.models import BroadcastType
        
        broadcast = self.get_object()
        if broadcast.status == "sent":
            return Response(
                {"error": "Broadcast already sent"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Determine broadcast type (default to email if not set)
        broadcast_type = broadcast.broadcast_type or BroadcastType.EMAIL.value
        
        # Send based on broadcast type
        if broadcast_type == BroadcastType.PUSH.value:
            result = send_broadcast_push_notification(broadcast)
        else:
            # Default to email
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
                "broadcast_type": broadcast_type,
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

        # Validate that the broadcast hasn't been sent already
        if broadcast.status == BroadcastStatus.SENT.value:
            return Response(
                {"error": "Broadcast already sent. Cannot reschedule."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse and validate the scheduled_at datetime
        try:
            from django.utils.dateparse import parse_datetime
            scheduled_datetime = parse_datetime(scheduled_at)
            
            # If Django's parse_datetime fails, try dateutil parser
            if not scheduled_datetime:
                try:
                    from dateutil import parser
                    scheduled_datetime = parser.parse(scheduled_at)
                except (ImportError, ValueError, TypeError):
                    return Response(
                        {"error": "Invalid datetime format. Please use ISO format (YYYY-MM-DDTHH:MM:SS)"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            
            if not scheduled_datetime:
                return Response(
                    {"error": "Invalid datetime format. Please use ISO format (YYYY-MM-DDTHH:MM:SS)"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            # Make timezone aware if it's naive
            if timezone.is_naive(scheduled_datetime):
                scheduled_datetime = timezone.make_aware(scheduled_datetime)
            
            # Validate that scheduled time is in the future
            if scheduled_datetime <= timezone.now():
                return Response(
                    {"error": "Scheduled time must be in the future"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
        except (ValueError, TypeError) as e:
            return Response(
                {"error": f"Invalid datetime format: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        broadcast.status = BroadcastStatus.PENDING.value
        broadcast.scheduled_at = scheduled_datetime
        broadcast.save()
        
        return Response({
            "status": "Broadcast scheduled successfully",
            "scheduled_at": scheduled_datetime.isoformat()
        })


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

    @action(detail=True, methods=["post"])
    def test_connection(self, request, pk=None):
        """Test gateway connection with provided credentials"""
        gateway = self.get_object()
        config = request.data.get("config", gateway.config or {})
        
        gateway_name_lower = gateway.name.lower()
        
        if "zaincash" in gateway_name_lower or "zain cash" in gateway_name_lower:
            # Test Zain Cash credentials
            merchant_id = config.get("merchantId", "")
            merchant_secret = config.get("merchantSecret", "")
            environment = config.get("environment", "test")
            msisdn = config.get("msisdn", "")
            
            if not merchant_id or not merchant_secret:
                return Response(
                    {"success": False, "message": "Merchant ID and Merchant Secret are required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            result = test_zaincash_credentials(merchant_id, merchant_secret, environment, msisdn)
            
            if result["success"]:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
        elif "stripe" in gateway_name_lower:
            # Test Stripe credentials
            secret_key = config.get("secretKey", "")
            publishable_key = config.get("publishableKey", "")
            
            if not secret_key:
                return Response(
                    {"success": False, "message": "Secret Key is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            result = test_stripe_credentials(secret_key, publishable_key)
            
            if result["success"]:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
        elif "qicard" in gateway_name_lower or "qi card" in gateway_name_lower or "qi-card" in gateway_name_lower:
            # Test QiCard credentials
            terminal_id = config.get("terminalId", "")
            username = config.get("username", "")
            password = config.get("password", "")
            environment = config.get("environment", "test")
            
            if not terminal_id or not username or not password:
                return Response(
                    {"success": False, "message": "Terminal ID, Username, and Password are required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            result = test_qicard_credentials(terminal_id, username, password, environment)
            
            if result["success"]:
                return Response(result, status=status.HTTP_200_OK)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
        else:
            # For other gateways, just validate that required fields are present
            # (PayTabs, etc. would need their own test implementations)
            return Response(
                {"success": True, "message": "Credentials validated (no API test available for this gateway)"},
                status=status.HTTP_200_OK,
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
    
    # Log initial request details
    logger.info("=" * 80)
    logger.info("PAYTABS RETURN URL CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"GET params: {dict(request.GET)}")
    logger.info(f"POST data: {dict(request.POST) if hasattr(request, 'POST') else 'N/A'}")
    logger.info(f"Request body: {request.body.decode('utf-8') if request.body else 'Empty'}")
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
                    logger.info(f"Updating existing payment: ID={payment.id}, Old status={payment.payment_status}, "
                               f"Old amount={payment.amount}, Old tran_ref={payment.tran_ref}")
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = tran_ref
                    payment.amount = amount
                    payment.save()
                    logger.info(f"Payment updated successfully: ID={payment.id}, New status={payment.payment_status}, "
                               f"New amount={payment.amount}, New tran_ref={payment.tran_ref}")
                else:
                    # Create new payment
                    logger.info(f"Creating new payment: subscription_id={subscription.id}, amount={amount}, "
                               f"gateway={paytabs_gateway.name if paytabs_gateway else 'None'}, tran_ref={tran_ref}")
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        payment_method=paytabs_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=tran_ref,
                    )
                    logger.info(f"Payment created successfully: ID={payment.id}, status={payment.payment_status}")
            
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


@api_view(["POST"])
def create_zaincash_payment(request):
    """
    Create a Zain Cash payment session for a subscription
    POST /api/payments/create-zaincash-session/
    Body: { subscription_id: int }
    """
    # Validate serializer
    serializer = CreateZaincashPaymentSerializer(data=request.data)
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
    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return Response(
            {"error": "Subscription is already active. Use renewal or plan change to proceed."},
            status=status.HTTP_400_BAD_REQUEST,
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
        f"Creating Zain Cash payment for subscription {subscription_id}: "
        f"plan={plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return Response(
            {"error": "Plan is free, no payment required"},
            status=status.HTTP_400_BAD_REQUEST,
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
            return Response(
                {"error": "Failed to create payment session: No transaction ID received"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        if not payment_url:
            # Construct payment URL if not provided
            environment = subscription.payment_method.config.get("environment", "test") if subscription.payment_method else "test"
            api_base_url = "https://api.zaincash.iq" if environment == "live" else "https://test.zaincash.iq"
            payment_url = f"{api_base_url}/transaction/pay?id={transaction_id}"
        
        # Get Zain Cash gateway using the utility function
        from .zaincash_utils import get_zaincash_gateway
        zaincash_gateway = get_zaincash_gateway()

        if not zaincash_gateway:
            logger.error("Zain Cash gateway not found after payment session creation")
            return Response(
                {"error": "Zain Cash gateway is not configured or enabled"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Create payment record
        payment = Payment.objects.create(
            subscription=subscription,
            amount=amount,
            payment_method=zaincash_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref=transaction_id,  # Store transaction ID
        )

        logger.info(
            f"Created Zain Cash payment record: payment_id={payment.id}, "
            f"transaction_id={transaction_id}, subscription_id={subscription_id}, "
            f"payment_url={payment_url}"
        )

        # Return the payment URL that frontend should redirect user to
        return Response(
            {
                "redirect_url": payment_url,
                "transaction_id": transaction_id,
            },
            status=status.HTTP_200_OK,
        )
    except requests.exceptions.HTTPError as e:
        error_details = {}
        try:
            error_details = e.response.json()
        except:
            error_details = {"message": e.response.text}

        return Response(
            {
                "error": f"Zain Cash API error: {error_details.get('message', str(e))}",
                "details": error_details,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except ValueError as e:
        # Handle configuration errors (gateway not found, credentials missing, etc.)
        logger.error(f"Zain Cash configuration error: {str(e)}")
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Zain Cash request error: {str(e)}", exc_info=True)
        return Response(
            {"error": f"Error communicating with Zain Cash: {str(e)}"},
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
                from .zaincash_utils import get_zaincash_gateway
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
                    from .zaincash_utils import get_zaincash_gateway
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
                return Response(
                    {
                        "status": "failed",
                        "message": error_message or "Payment failed",
                        "subscription_id": subscription_id,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
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
                return Response(
                    {"status": "error", "message": "Invalid transaction: Missing order ID"},
                    status=status.HTTP_400_BAD_REQUEST,
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
            from .zaincash_utils import get_zaincash_gateway
            zaincash_gateway = get_zaincash_gateway()

            # Find existing payment first (it should exist from when we created the session)
            payment = None
            amount = 0.0
            if zaincash_gateway:
                payment = (
                    Payment.objects.filter(
                        subscription=subscription, payment_method=zaincash_gateway
                    )
                    .order_by("-created_at")
                    .first()
                )

                if payment:
                    # Get amount from existing payment record
                    amount = float(payment.amount)
                    # Update existing payment
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = token  # Store the JWT token from Zain Cash
                    payment.save()
                else:
                    # Payment record doesn't exist - get amount from subscription plan
                    # This shouldn't normally happen, but handle it gracefully
                    plan = subscription.plan
                    # Try to determine billing cycle from subscription or default to monthly
                    if subscription.end_date and subscription.start_date:
                        days_diff = (subscription.end_date - subscription.start_date).days
                        billing_cycle = "yearly" if days_diff >= 330 else "monthly"
                    else:
                        billing_cycle = "monthly"  # Default to monthly
                    
                    amount = float(plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly)
                    
                    # Create new payment record
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        payment_method=zaincash_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=token,
                    )
                    logger.warning(
                        f"Created new payment record for subscription {subscription_id} - "
                        f"this shouldn't normally happen as payment should exist from session creation"
                    )
            
            # Determine billing cycle based on payment amount
            plan = subscription.plan
            billing_cycle = None
            
            if amount > 0:
                if abs(amount - float(plan.price_yearly)) < 0.01:
                    billing_cycle = "yearly"
                elif abs(amount - float(plan.price_monthly)) < 0.01:
                    billing_cycle = "monthly"
                else:
                    yearly_diff = abs(amount - float(plan.price_yearly))
                    monthly_diff = abs(amount - float(plan.price_monthly))
                    billing_cycle = "yearly" if yearly_diff < monthly_diff else "monthly"
                    logger.info(
                        f"Payment amount {amount} doesn't exactly match plan prices "
                        f"(yearly: {plan.price_yearly}, monthly: {plan.price_monthly}). "
                        f"Using {billing_cycle} based on closest match."
                    )
            else:
                # If amount is still 0, default to monthly
                billing_cycle = "monthly"
                amount = float(plan.price_monthly)
                logger.warning(
                    f"Payment amount was 0, using default monthly price: {amount}"
                )

            # Calculate new end date using the same logic as PayTabs and Stripe
            now = timezone.now()
            
            # Check if subscription has any completed payments
            has_completed_payments = Payment.objects.filter(
                subscription=subscription,
                payment_status=PaymentStatus.COMPLETED.value
            ).exists()
            
            # Determine base_date based on subscription state:
            # 1. Renewal: has completed payments AND end_date is in the future -> extend from end_date
            # 2. Reactivation: has completed payments BUT end_date is in the past -> start from now
            # 3. New subscription: no completed payments -> start from now or start_date
            if has_completed_payments and subscription.end_date and subscription.end_date > now:
                # This is a renewal - extend from existing end_date
                base_date = subscription.end_date
                logger.info(
                    f"Subscription {subscription.id} is a RENEWAL - extending from end_date ({subscription.end_date.strftime('%Y-%m-%d %H:%M:%S')})"
                )
            elif has_completed_payments and (not subscription.end_date or subscription.end_date <= now):
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
            # Only update start_date if it's a new subscription
            if not subscription.start_date or subscription.start_date <= now:
                subscription.start_date = now
            subscription.save(update_fields=['is_active', 'end_date', 'start_date', 'updated_at'])
            
            # Refresh from DB to verify update
            subscription.refresh_from_db()
            logger.info(f"AFTER UPDATE - Subscription {subscription.id}: is_active={subscription.is_active}, "
                       f"end_date={subscription.end_date.strftime('%Y-%m-%d %H:%M:%S') if subscription.end_date else 'None'}")
            
            logger.info(
                f"✓ Activated subscription {subscription.id} for company {subscription.company.name if subscription.company else 'None'}. "
                f"Billing cycle: {billing_cycle}, Amount: {amount}, "
                f"End date set to: {new_end_date.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # Create invoice
            Invoice.objects.create(
                subscription=subscription,
                amount=amount,
                due_date=new_end_date,
                status=InvoiceStatus.PAID.value,
            )

            logger.info(
                f"Zain Cash payment completed for subscription {subscription_id}, amount: {amount}, billing_cycle: {billing_cycle}"
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
                return Response(
                    {
                        "status": "success",
                        "message": "Payment completed successfully",
                        "subscription_id": subscription_id,
                        "subscription_active": subscription.is_active,
                    },
                    status=status.HTTP_200_OK,
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
                return Response(
                    {
                        "status": "error",
                        "message": "Payment status unknown",
                        "subscription_id": subscription_id,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
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

        # Get payment - handle case where payment might not exist yet
        # Find payment by subscription (regardless of gateway)
        try:
            payment = (
                Payment.objects.filter(subscription=subscription)
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
        gateway_status = None

        # PRIORITY: If subscription is active, payment MUST be completed
        # This is the source of truth - if subscription is active, payment is done
        if subscription.is_active:
            payment_status_value = PaymentStatus.COMPLETED.value
            paytabs_status = "A"  # For PayTabs compatibility
            gateway_status = "success"  # Generic success status
            logger.info(
                f"Subscription {subscription_id} is ACTIVE - returning completed status immediately"
            )
        elif payment and payment.tran_ref:
            # Check payment gateway type and verify accordingly
            payment_gateway = payment.payment_method
            if payment_gateway:
                gateway_name = payment_gateway.name.lower()
                
                # If it's a Zain Cash payment, check status using transaction ID
                if "zain" in gateway_name or "zaincash" in gateway_name:
                    try:
                        from .zaincash_utils import check_zaincash_payment_status
                        result = check_zaincash_payment_status(payment.tran_ref)
                        gateway_status = result.get("status", "pending")
                        if gateway_status == "success":
                            payment_status_value = PaymentStatus.COMPLETED.value
                            # Also update payment record if status changed
                            if payment.payment_status != PaymentStatus.COMPLETED.value:
                                payment.payment_status = PaymentStatus.COMPLETED.value
                                payment.save()
                                # Activate subscription if payment is successful
                                if not subscription.is_active:
                                    subscription.is_active = True
                                    subscription.start_date = timezone.now()
                                    subscription.save()
                    except Exception as e:
                        logger.warning(f"Could not verify Zain Cash payment: {str(e)}")
                        # If payment is completed in DB but verification fails, assume approved
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"
                
                # If it's a PayTabs payment, verify with PayTabs
                elif "paytabs" in gateway_name:
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
                
                # If it's a Stripe payment, verify with Stripe
                elif "stripe" in gateway_name:
                    try:
                        # For Stripe, tran_ref is the session_id
                        result = verify_stripe_payment(payment.tran_ref)
                        stripe_status = result.get("stripe_payment_status")
                        if stripe_status == "paid":
                            gateway_status = "success"
                            payment_status_value = PaymentStatus.COMPLETED.value
                            # Also update payment record if status changed
                            if payment.payment_status != PaymentStatus.COMPLETED.value:
                                payment.payment_status = PaymentStatus.COMPLETED.value
                                payment.save()
                                # Activate subscription if payment is successful
                                if not subscription.is_active:
                                    subscription.is_active = True
                                    subscription.start_date = timezone.now()
                                    subscription.save()
                    except Exception as e:
                        logger.warning(f"Could not verify Stripe payment: {str(e)}")
                        # If payment is completed in DB but verification fails, assume approved
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"

            # If payment is completed in DB, ensure status is set
            if (
                payment.payment_status == PaymentStatus.COMPLETED.value
                and not paytabs_status
                and not gateway_status
            ):
                paytabs_status = "A"  # For PayTabs compatibility
                gateway_status = "success"  # Generic success

        # Return all fields the frontend needs - ensure values match what frontend expects
        # Check if subscription is truly active (considering end_date)
        is_truly_active = subscription.is_truly_active()
        days_until_expiry = subscription.days_until_expiry()
        is_expiring_soon = subscription.is_expiring_soon(days_threshold=30)
        
        # If subscription is not truly active but is_active=True, update it
        if subscription.is_active and not is_truly_active:
            subscription.is_active = False
            subscription.save(update_fields=['is_active', 'updated_at'])
            subscription.refresh_from_db()
            is_truly_active = False
            logger.info(f"Subscription {subscription_id} was marked as inactive due to expired end_date")

        response_data = {
            "subscription_id": subscription_id,
            "subscription_active": bool(subscription.is_active),
            "is_truly_active": is_truly_active,
            "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
            "days_until_expiry": days_until_expiry,
            "is_expiring_soon": is_expiring_soon,
            "payment_status": payment_status_value,
            "paytabs_status": paytabs_status,
            "gateway_status": gateway_status,
            "payment_exists": payment is not None,
        }

        logger.info(
            f"Payment status check for subscription {subscription_id}: subscription.is_active={subscription.is_active}, "
            f"is_truly_active={is_truly_active}, days_until_expiry={days_until_expiry}, "
            f"payment_status={payment_status_value}, paytabs_status={paytabs_status}, payment exists={payment is not None}"
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
    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return Response(
            {"error": "Subscription is already active. Use renewal or plan change to proceed."},
            status=status.HTTP_400_BAD_REQUEST,
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
        return Response(
            {"error": "Plan is free, no payment required"},
            status=status.HTTP_400_BAD_REQUEST,
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
                return Response(
                    {"error": "Stripe gateway is not configured or enabled"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            # Create payment record
            session_id = result.get("session_id", "")
            payment = Payment.objects.create(
                subscription=subscription,
                amount=amount,
                payment_method=stripe_gateway,
                payment_status=PaymentStatus.PENDING.value,
                tran_ref=session_id,  # Store session_id as tran_ref
            )

            return Response(
                {
                    "payment_id": payment.id,
                    "redirect_url": result.get("url"),
                    "session_id": session_id,
                }
            )
        else:
            # Error response from Stripe
            error_msg = (
                result.get("error")
                or result.get("message")
                or "Failed to create payment session"
            )
            return Response(
                {"error": error_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
    except Exception as e:
        logger.error(f"Error creating Stripe payment: {str(e)}", exc_info=True)
        return Response(
            {"error": f"Error creating Stripe payment: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
                    # Update existing payment
                    logger.info(f"Updating existing payment: ID={payment.id}, Old status={payment.payment_status}, "
                               f"Old amount={payment.amount}, Old tran_ref={payment.tran_ref}")
                    payment.payment_status = PaymentStatus.COMPLETED.value
                    payment.tran_ref = session_id
                    payment.amount = amount
                    payment.save()
                    logger.info(f"Payment updated successfully: ID={payment.id}, New status={payment.payment_status}, "
                               f"New amount={payment.amount}, New tran_ref={payment.tran_ref}")
                else:
                    # Create new payment
                    logger.info(f"Creating new payment: subscription_id={subscription.id}, amount={amount}, "
                               f"gateway={stripe_gateway.name if stripe_gateway else 'None'}, session_id={session_id}")
                    payment = Payment.objects.create(
                        subscription=subscription,
                        amount=amount,
                        payment_method=stripe_gateway,
                        payment_status=PaymentStatus.COMPLETED.value,
                        tran_ref=session_id,
                    )
                    logger.info(f"Payment created successfully: ID={payment.id}, status={payment.payment_status}")
            
            # Determine billing cycle based on payment amount
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


@api_view(["POST"])
def create_qicard_payment(request):
    """
    Create a QiCard payment session for a subscription
    POST /api/payments/create-qicard-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: 'monthly' | 'yearly' }
    """
    # Validate serializer
    serializer = CreateQicardPaymentSerializer(data=request.data)
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
    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return Response(
            {"error": "Subscription is already active. Use renewal or plan change to proceed."},
            status=status.HTTP_400_BAD_REQUEST,
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
        f"Creating QiCard payment for subscription {subscription_id}: "
        f"plan={plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return Response(
            {"error": "Plan is free, no payment required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prepare QiCard payment request
    user_email = subscription.company.owner.email
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username

    # QiCard redirects to BACKEND return URL first, then we redirect to frontend
    return_url = f"{settings.API_BASE_URL}/api/payments/qicard-return/?subscription_id={subscription_id}"
    # Webhook URL for payment notifications
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

        # Log the response for debugging
        logger.info(f"QiCard API response: {result}")
        
        # Extract payment ID and form URL from result
        payment_id = result.get("payment_id")
        form_url = result.get("form_url")
        request_id = result.get("request_id")
        
        if not payment_id or not form_url:
            logger.error(f"QiCard API did not return payment ID or form URL. Response: {result}")
            return Response(
                {"error": "Failed to create payment session: No payment ID or form URL received"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Get QiCard gateway using the utility function
        from .qicard_utils import get_qicard_gateway
        qicard_gateway = get_qicard_gateway()

        if not qicard_gateway:
            logger.error("QiCard gateway not found after payment session creation")
            return Response(
                {"error": "QiCard gateway is not configured or enabled"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Create payment record
        payment = Payment.objects.create(
            subscription=subscription,
            amount=amount,
            payment_method=qicard_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref=payment_id,  # Store payment ID as tran_ref
        )

        logger.info(
            f"Created QiCard payment record: payment_id={payment.id}, "
            f"qicard_payment_id={payment_id}, subscription_id={subscription_id}, "
            f"form_url={form_url}"
        )

        # Return the form URL that frontend should redirect user to
        return Response(
            {
                "redirect_url": form_url,
                "payment_id": payment_id,
                "request_id": request_id,
            },
            status=status.HTTP_200_OK,
        )
    except requests.exceptions.HTTPError as e:
        error_details = {}
        try:
            error_details = e.response.json()
        except:
            error_details = {"message": e.response.text}

        return Response(
            {
                "error": f"QiCard API error: {error_details.get('message', str(e))}",
                "details": error_details,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except ValueError as e:
        # Handle configuration errors (gateway not found, credentials missing, etc.)
        logger.error(f"QiCard configuration error: {str(e)}")
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"QiCard request error: {str(e)}", exc_info=True)
        return Response(
            {"error": f"Error communicating with QiCard: {str(e)}"},
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
        from .qicard_utils import verify_qicard_payment, get_qicard_gateway
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
                        
                        payment = Payment.objects.create(
                            subscription=subscription,
                            amount=original_amount,  # Store original amount in USD
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
            
            # Update subscription end date and status
            # Use the same logic as Paytabs and Stripe for consistency
            subscription = payment.subscription
            plan = subscription.plan
            
            if plan:
                # Get payment amount (in USD) to determine billing cycle
                payment_amount = float(payment.amount)
                
                # Determine billing cycle based on payment amount (same as Paytabs/Stripe)
                billing_cycle = None
                if abs(payment_amount - float(plan.price_yearly)) < 0.01:
                    billing_cycle = "yearly"
                elif abs(payment_amount - float(plan.price_monthly)) < 0.01:
                    billing_cycle = "monthly"
                else:
                    # If amount doesn't match exactly, determine by comparing which is closer
                    yearly_diff = abs(payment_amount - float(plan.price_yearly))
                    monthly_diff = abs(payment_amount - float(plan.price_monthly))
                    billing_cycle = "yearly" if yearly_diff < monthly_diff else "monthly"
                    logger.warning(
                        f"Payment amount {payment_amount} doesn't exactly match plan prices "
                        f"(yearly: {plan.price_yearly}, monthly: {plan.price_monthly}). "
                        f"Using {billing_cycle} based on closest match."
                    )
                
                logger.info(f"Determined billing_cycle: {billing_cycle} for subscription {subscription.id} "
                           f"(payment_amount: {payment_amount}, plan.yearly: {plan.price_yearly}, plan.monthly: {plan.price_monthly})")
                
                # Calculate correct end_date based on billing cycle (same logic as Paytabs/Stripe)
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
                
                # Determine base_date based on subscription state (same as Paytabs/Stripe):
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
                
                # Calculate new end_date based on billing cycle
                if billing_cycle == "yearly":
                    new_end_date = base_date + timedelta(days=365)
                else:
                    new_end_date = base_date + timedelta(days=30)
                
                # Update subscription with correct end_date and activate it (same as Paytabs/Stripe)
                old_is_active = subscription.is_active
                old_end_date = subscription.end_date
                logger.info(f"BEFORE UPDATE - Subscription {subscription.id}: is_active={old_is_active}, "
                           f"end_date={old_end_date.strftime('%Y-%m-%d %H:%M:%S') if old_end_date else 'None'}")
                
                subscription.is_active = True
                subscription.end_date = new_end_date
                subscription.save(update_fields=['is_active', 'end_date', 'updated_at'])
                
                # Refresh from DB to verify update (same as Paytabs/Stripe)
                subscription.refresh_from_db()
                logger.info(f"AFTER UPDATE - Subscription {subscription.id}: is_active={subscription.is_active}, "
                           f"end_date={subscription.end_date.strftime('%Y-%m-%d %H:%M:%S') if subscription.end_date else 'None'}")
                
                logger.info(
                    f"✓ Activated subscription {subscription.id} for company {subscription.company.name if subscription.company else 'None'}. "
                    f"Billing cycle: {billing_cycle}, Amount: {payment_amount}, "
                    f"End date set to: {new_end_date.strftime('%Y-%m-%d %H:%M:%S')}"
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
    Handle QiCard webhook notifications
    POST /api/payments/qicard-webhook/
    QiCard sends payment status updates via webhook
    """
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 80)
    logger.info("QICARD WEBHOOK CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request body: {request.body.decode('utf-8') if request.body else 'Empty'}")
    logger.info("=" * 80)

    try:
        # Parse webhook payload
        if hasattr(request, 'data') and request.data:
            payload = request.data
        else:
            try:
                payload = json.loads(request.body.decode('utf-8'))
            except:
                payload = {}
        
        logger.info(f"QiCard webhook payload: {json.dumps(payload, indent=2, default=str)}")
        
        # Extract payment information from webhook
        payment_id = payload.get("paymentId")
        status = payload.get("status")
        request_id = payload.get("requestId")
        
        if not payment_id:
            logger.error("Missing paymentId in QiCard webhook")
            return Response(
                {"error": "Missing paymentId"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Find payment by tran_ref (which stores payment_id)
        payment = Payment.objects.filter(tran_ref=payment_id).order_by("-created_at").first()
        
        if not payment:
            logger.warning(f"Payment not found for QiCard payment_id: {payment_id}")
            return Response(
                {"error": "Payment not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        subscription = payment.subscription
        
        logger.info(f"Found payment: ID={payment.id}, subscription_id={subscription.id}, "
                   f"status={status}, current_payment_status={payment.payment_status}")
        
        # Update payment status based on QiCard status
        if status == "SUCCESS":
            payment.payment_status = PaymentStatus.COMPLETED.value
            payment.save(update_fields=['payment_status', 'updated_at'])
            
            # Activate subscription
            if not subscription.is_active:
                subscription.is_active = True
                subscription.start_date = timezone.now()
                
                # Calculate end date based on billing cycle
                days_diff = (subscription.end_date - subscription.start_date).days if subscription.end_date else 0
                billing_cycle = "yearly" if days_diff >= 330 else "monthly"
                
                if billing_cycle == "yearly":
                    subscription.end_date = subscription.start_date + timedelta(days=365)
                else:
                    subscription.end_date = subscription.start_date + timedelta(days=30)
                
                subscription.save(update_fields=['is_active', 'start_date', 'end_date', 'updated_at'])
                logger.info(f"Activated subscription {subscription.id}, end_date={subscription.end_date}")
            
            logger.info(f"Payment {payment.id} marked as COMPLETED")
        elif status == "FAILED" or status == "AUTHENTICATION_FAILED":
            payment.payment_status = PaymentStatus.FAILED.value
            payment.save(update_fields=['payment_status', 'updated_at'])
            logger.info(f"Payment {payment.id} marked as FAILED")
        else:
            # CREATED or other status - keep as PENDING
            logger.info(f"Payment {payment.id} status is {status}, keeping as PENDING")
        
        return Response({"success": True}, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"ERROR processing QiCard webhook: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback:", exc_info=True)
        logger.error("=" * 80)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
