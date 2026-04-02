import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import viewsets, filters, generics, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from crm_saas_api.responses import error_response, success_response, validation_error_response

from ..services import payment_gateway_test_response, deactivate_other_subscriptions_for_company
from ..models import (
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
from ..serializers import (
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
)
from accounts.permissions import (
    CanManagePlans,
    CanManageSubscriptions,
    CanManagePayments,
    CanManagePaymentGateways,
    CanManageCommunication,
)
from ..utils import send_broadcast_email
from ..zaincash_utils import test_zaincash_credentials
from ..stripe_utils import test_stripe_credentials
from ..qicard_utils import test_qicard_credentials
from ..fib_utils import test_fib_credentials

logger = logging.getLogger(__name__)


class PlanViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Plan instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Plan.objects.all()
    permission_classes = [IsAuthenticated, CanManagePlans]
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


def deactivate_other_subscriptions_for_company(company_id, exclude_subscription_id=None):
    """Ensure only one subscription per company is active: deactivate all others for this company."""
    qs = Subscription.objects.filter(company_id=company_id)
    if exclude_subscription_id is not None:
        qs = qs.exclude(pk=exclude_subscription_id)
    updated = qs.filter(is_active=True).update(is_active=False)
    if updated:
        logger.info(
            "Deactivated %s other subscription(s) for company_id=%s (only one active per company)",
            updated,
            company_id,
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def switch_subscription_plan_free(request):
    """
    Switch the current company's active subscription to a FREE/TRIAL plan (no payment, no billing cycle).
    POST /api/subscriptions/switch-plan-free/
    Body: { "plan_id": <int> }
    """
    user = request.user
    company = getattr(user, "company", None)
    if not company:
        return error_response(
            "User is not associated with a company.",
            code="no_company",
        )

    plan_id = request.data.get("plan_id")
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return error_response("plan_id is required.", code="missing_plan_id")

    try:
        plan = Plan.objects.get(id=plan_id, visible=True)
    except Plan.DoesNotExist:
        return error_response("Plan not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)

    # Only allow free/trial plans in this endpoint
    is_free_or_trial = float(plan.price_monthly) <= 0 and float(plan.price_yearly) <= 0
    if not is_free_or_trial:
        return error_response(
            "Selected plan requires payment.",
            code="payment_required",
            details={"requires_payment": True},
        )

    # Find current subscription (prefer active; otherwise latest)
    subscription = (
        Subscription.objects.filter(company=company, is_active=True)
        .order_by("-created_at")
        .first()
    ) or Subscription.objects.filter(company=company).order_by("-created_at").first()

    if not subscription:
        return error_response("Subscription not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)

    # Ensure only one active subscription for the company
    deactivate_other_subscriptions_for_company(company.id, exclude_subscription_id=subscription.id)

    now = timezone.now()
    # Update plan + dates
    subscription.plan = plan
    subscription.is_active = True
    subscription.start_date = now
    if int(getattr(plan, "trial_days", 0) or 0) > 0:
        subscription.end_date = now + timedelta(days=int(plan.trial_days))
    else:
        subscription.end_date = now + timedelta(days=365 * 100)
    subscription.auto_renew = False
    subscription.save(update_fields=["plan", "is_active", "start_date", "end_date", "auto_renew", "updated_at"])

    return success_response(
        data={
            "subscription_id": subscription.id,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
            "is_active": bool(subscription.is_active),
        },
    )


class SubscriptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Subscription instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage subscriptions
    """

    queryset = Subscription.objects.all()
    permission_classes = [IsAuthenticated, CanManageSubscriptions]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["company__name", "plan__name"]
    ordering_fields = ["created_at", "start_date", "end_date"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return SubscriptionListSerializer
        return SubscriptionSerializer

    def perform_create(self, serializer):
        validated = serializer.validated_data
        if validated.get("is_active", True):
            company_id = validated["company"].id
            deactivate_other_subscriptions_for_company(company_id, exclude_subscription_id=None)
        serializer.save()

    def perform_update(self, serializer):
        instance = serializer.instance
        validated = serializer.validated_data
        # When this subscription is (or will be) active, ensure no other active sub for same company
        will_be_active = validated.get("is_active", instance.is_active)
        if will_be_active:
            company_id = instance.company_id
            deactivate_other_subscriptions_for_company(company_id, exclude_subscription_id=instance.id)
        serializer.save()


class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Payment instances.
    Provides CRUD operations: Create, Read, Update, Delete
    Only Super Admin can manage payments
    """

    queryset = Payment.objects.all()
    permission_classes = [IsAuthenticated, CanManagePayments]
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
    permission_classes = [IsAuthenticated, CanManagePayments]
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
        return success_response(data={"status": "Invoice marked as paid"})


class BroadcastViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Broadcast instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Broadcast.objects.all()
    permission_classes = [IsAuthenticated, CanManageCommunication]
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
            return error_response("Broadcast already sent", code="bad_request")

        # Determine broadcast type (default to email if not set)
        broadcast_type = broadcast.broadcast_type or BroadcastType.EMAIL.value
        
        # Send based on broadcast type
        if broadcast_type == BroadcastType.PUSH.value:
            result = send_broadcast_push_notification(broadcast)
        else:
            # Default to email
            result = send_broadcast_email(broadcast)

        if not result["success"]:
            return error_response(
                result.get("error", "Failed to send broadcast"),
                code="bad_request",
            )

        # Update broadcast status
        broadcast.status = BroadcastStatus.SENT.value
        broadcast.sent_at = timezone.now()
        broadcast.save()

        return success_response(
            data={
                "status": "Broadcast sent successfully",
                "recipients_count": result.get("recipients_count", 0),
                "broadcast_type": broadcast_type,
            },
        )

    @action(detail=False, methods=["post"], url_path="send-sms")
    def send_sms(self, request):
        """
        Send SMS to users matching targets (same as broadcast: all, plan_X, role_*, company_X).
        Body: { "targets": ["all"] | ["company_1", ...], "content": "message text" }.
        Uses platform Twilio settings.
        """
        from subscriptions.utils import send_broadcast_sms

        targets = request.data.get("targets")
        content = request.data.get("content") or ""
        if not isinstance(targets, list):
            targets = ["all"] if not targets else [targets] if isinstance(targets, str) else []
        if not targets:
            targets = ["all"]
        content = (content or "").strip()
        if not content:
            return error_response(
                "Message content is required.",
                code="bad_request",
            )
        result = send_broadcast_sms(targets, content)
        if not result["success"]:
            return error_response(
                result.get("error", "Failed to send SMS."),
                code="bad_request",
                details={
                    "sent_count": result.get("sent_count", 0),
                    "skipped_count": result.get("skipped_count", 0),
                },
            )
        return success_response(
            data={
                "sent_count": result.get("sent_count", 0),
                "skipped_count": result.get("skipped_count", 0),
            },
        )

    @action(detail=True, methods=["post"])
    def schedule(self, request, pk=None):
        """Schedule a broadcast for later"""
        broadcast = self.get_object()
        scheduled_at = request.data.get("scheduled_at")
        if not scheduled_at:
            return error_response(
                "scheduled_at is required",
                code="bad_request",
            )

        # Validate that the broadcast hasn't been sent already
        if broadcast.status == BroadcastStatus.SENT.value:
            return error_response(
                "Broadcast already sent. Cannot reschedule.",
                code="bad_request",
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
                    return error_response(
                        "Invalid datetime format. Please use ISO format (YYYY-MM-DDTHH:MM:SS)",
                        code="bad_request",
                    )
            
            if not scheduled_datetime:
                return error_response(
                    "Invalid datetime format. Please use ISO format (YYYY-MM-DDTHH:MM:SS)",
                    code="bad_request",
                )
            
            # Make timezone aware if it's naive
            if timezone.is_naive(scheduled_datetime):
                scheduled_datetime = timezone.make_aware(scheduled_datetime)
            
            # Validate that scheduled time is in the future
            if scheduled_datetime <= timezone.now():
                return error_response(
                    "Scheduled time must be in the future",
                    code="bad_request",
                )
            
        except (ValueError, TypeError) as e:
            return error_response(
                f"Invalid datetime format: {str(e)}",
                code="bad_request",
            )

        broadcast.status = BroadcastStatus.PENDING.value
        broadcast.scheduled_at = scheduled_datetime
        broadcast.save()
        
        return success_response(
            data={
                "status": "Broadcast scheduled successfully",
                "scheduled_at": scheduled_datetime.isoformat(),
            },
        )


class PaymentGatewayViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing PaymentGateway instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = PaymentGateway.objects.all()
    permission_classes = [IsAuthenticated, CanManagePaymentGateways]
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
            return error_response(
                "Gateway setup required before enabling",
                code="bad_request",
            )

        gateway.enabled = not gateway.enabled
        gateway.status = (
            PaymentGatewayStatus.ACTIVE.value
            if gateway.enabled
            else PaymentGatewayStatus.DISABLED.value
        )
        gateway.save()
        return success_response(
            data={
                "status": f'Gateway {"enabled" if gateway.enabled else "disabled"}',
                "enabled": gateway.enabled,
            },
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
                return error_response(
                    "Merchant ID and Merchant Secret are required",
                    code="bad_request",
                    details={"success": False},
                )
            
            result = test_zaincash_credentials(merchant_id, merchant_secret, environment, msisdn)
            
            return payment_gateway_test_response(result)
        elif "stripe" in gateway_name_lower:
            # Test Stripe credentials
            secret_key = config.get("secretKey", "")
            publishable_key = config.get("publishableKey", "")
            
            if not secret_key:
                return error_response(
                    "Secret Key is required",
                    code="bad_request",
                    details={"success": False},
                )
            
            result = test_stripe_credentials(secret_key, publishable_key)
            
            return payment_gateway_test_response(result)
        elif "qicard" in gateway_name_lower or "qi card" in gateway_name_lower or "qi-card" in gateway_name_lower:
            # Test QiCard credentials
            terminal_id = config.get("terminalId", "")
            username = config.get("username", "")
            password = config.get("password", "")
            environment = config.get("environment", "test")
            
            if not terminal_id or not username or not password:
                return error_response(
                    "Terminal ID, Username, and Password are required",
                    code="bad_request",
                    details={"success": False},
                )
            
            result = test_qicard_credentials(terminal_id, username, password, environment)
            
            return payment_gateway_test_response(result)
        elif "fib" in gateway_name_lower or "first iraqi" in gateway_name_lower:
            # Test FIB credentials
            client_id = config.get("clientId", "")
            client_secret = config.get("clientSecret", "")
            environment = config.get("environment", "test")
            
            if not client_id or not client_secret:
                return error_response(
                    "Client ID and Client Secret are required",
                    code="bad_request",
                    details={"success": False},
                )
            
            result = test_fib_credentials(client_id, client_secret, environment)
            
            return payment_gateway_test_response(result)
        else:
            # For other gateways, just validate that required fields are present
            # (PayTabs, etc. would need their own test implementations)
            return success_response(
                message="Credentials validated (no API test available for this gateway)",
                data={"validated": True},
            )


