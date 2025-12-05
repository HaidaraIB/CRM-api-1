from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import generics
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import redirect
import json
import requests
import logging

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
    PaytabsCallbackSerializer,
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

    # Check if subscription is already active (payment already completed)
    if subscription.is_active:
        return Response(
            {"error": "Subscription is already active"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Note: We allow any request with valid subscription_id
    # The subscription must be inactive (not paid yet) to proceed

    plan = subscription.plan

    days_diff = (subscription.end_date - subscription.start_date).days
    billing_cycle = "yearly" if days_diff >= 365 else "monthly"
    amount = float(
        plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly
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

    callback_url = f"{settings.PAYTABS_CALLBACK_URL}?subscription_id={subscription_id}"
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
            callback_url=callback_url,
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
def paytabs_callback(request):
    """
    Handle Paytabs webhook callback
    POST /api/payments/paytabs-callback/ (server-to-server callback)
    Paytabs sends JSON in request body
    """
    logger = logging.getLogger(__name__)

    try:
        # Parse data from multiple sources (PayTabs can send via GET query params or POST body)
        payload = {}
        
        # First, try to get from query parameters
        if request.GET:
            payload = dict(request.GET)
            # Convert list values to single values (Django QueryDict returns lists)
            for key, value in payload.items():
                if isinstance(value, list) and len(value) > 0:
                    payload[key] = value[0]
        
        # Then try POST data
        if request.method == "POST" and request.POST:
            post_data = dict(request.POST)
            for key, value in post_data.items():
                if isinstance(value, list) and len(value) > 0:
                    post_data[key] = value[0]
            payload.update(post_data)
        
        # Finally, try JSON body (PayTabs sends JSON for server-to-server callbacks)
        if request.body:
            try:
                json_payload = json.loads(request.body.decode("utf-8"))
                payload.update(json_payload)
            except Exception as e:
                logger.warning(f"Failed to parse JSON body: {str(e)}")
                # Try request.data (DRF parsed data)
                try:
                    if hasattr(request, 'data'):
                        payload.update(dict(request.data))
                except:
                    pass

        # Try multiple possible field names for tran_ref from all sources
        tran_ref = (
            payload.get("tran_ref")
            or payload.get("tranRef")
            or payload.get("tranref")
            or request.GET.get("tran_ref")
            or request.GET.get("tranRef")
            or request.GET.get("tranref")
            or request.POST.get("tran_ref")
            or request.POST.get("tranRef")
            or request.POST.get("tranref")
        )
        
        # Also try from request.data if available (DRF)
        if not tran_ref and hasattr(request, 'data'):
            try:
                tran_ref = (
                    request.data.get("tran_ref")
                    or request.data.get("tranRef")
                    or request.data.get("tranref")
                )
            except:
                pass

        logger.info(
            f"PayTabs callback - Method: {request.method}, Payload: {payload}, Query: {request.GET.dict()}, POST: {request.POST.dict() if hasattr(request, 'POST') else {}}"
        )

        if not tran_ref:
            # Log all available data for debugging
            logger.error(
                f"Missing tran_ref - Full request data: {dict(request.GET)}, Body: {request.body.decode('utf-8') if request.body else 'None'}"
            )
            return Response(
                {"error": "Missing tran_ref", "received_data": payload},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get Paytabs gateway
        paytabs_gateway = PaymentGateway.objects.filter(
            name__icontains="paytabs", enabled=True
        ).first()

        if not paytabs_gateway:
            logger.error("Paytabs gateway not found")
            return Response(
                {"error": "Paytabs gateway not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = verify_paytabs_payment(tran_ref)

        logger.info(f"PayTabs verified result: {result}")

        # Extract cart_id from verified result
        cart_id = result.get("cart_id")
        if not cart_id:
            logger.error("Missing cart_id in verified result")
            return Response(
                {"error": "Invalid transaction result"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Extract subscription ID from cart_id (format: SUB-{id})
        try:
            subscription_id = int(cart_id.replace("SUB-", ""))
            subscription = Subscription.objects.get(id=subscription_id)
        except (ValueError, Subscription.DoesNotExist) as e:
            logger.error(f"Invalid subscription ID: {cart_id}, error: {str(e)}")
            return Response(
                {"error": "Invalid subscription ID"}, status=status.HTTP_400_BAD_REQUEST
            )

        amount = float(result.get("cart_amount", 0))
        # Find or create payment record using tran_ref or subscription
        payment, created = Payment.objects.get_or_create(
            subscription=subscription,
            payment_method=paytabs_gateway,
            tran_ref=tran_ref,
            amount=amount,
            payment_status=PaymentStatus.PENDING.value,
        )

        if created:
            logger.info(f"Created new payment record: {payment.id}")

        # Update payment status based on verified result
        status_code = result.get("payment_result", {}).get("response_status")
        if status_code == "A":  # Approved
            payment.payment_status = PaymentStatus.COMPLETED.value
            payment.save()

            subscription.is_active = True
            subscription.save()

            # Mark company registration as completed
            company = subscription.company
            if company:
                company.registration_completed = True
                company.registration_completed_at = timezone.now()
                company.save()

            logger.info(
                f"Payment successful - tran_ref: {tran_ref}, subscription_id: {subscription_id}"
            )
        else:
            payment.payment_status = PaymentStatus.FAILED.value
            payment.save()
            logger.warning(
                f"Payment failed - tran_ref: {tran_ref}, status: {status_code}"
            )

        return Response({"status": "ok"})

    except Exception as e:
        logger.error(f"Error processing callback: {str(e)}", exc_info=True)
        return Response(
            {"error": f"Error processing callback: {str(e)}"},
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
        
        # Then try POST data
        if request.method == "POST" and request.POST:
            payload.update(dict(request.POST))
            for key, value in payload.items():
                if isinstance(value, list) and len(value) > 0:
                    payload[key] = value[0]
        
        # Finally, try JSON body
        if request.body:
            try:
                json_payload = json.loads(request.body.decode("utf-8"))
                payload.update(json_payload)
            except Exception:
                pass

        # Extract tran_ref from multiple possible field names and sources
        # PayTabs might send it in various formats
        tran_ref = (
            payload.get("tran_ref")
            or payload.get("tranRef")
            or payload.get("tranref")
            or payload.get("tranRef")
            or payload.get("transaction_ref")
            or payload.get("transactionRef")
            or payload.get("transactionReference")
            or request.GET.get("tran_ref")
            or request.GET.get("tranRef")
            or request.GET.get("tranref")
            or request.GET.get("transaction_ref")
            or request.GET.get("transactionRef")
            or request.POST.get("tran_ref")
            or request.POST.get("tranRef")
            or request.POST.get("tranref")
            or request.POST.get("transaction_ref")
            or request.POST.get("transactionRef")
        )
        
        # Also check if it's in the raw query string
        if not tran_ref and request.META.get('QUERY_STRING'):
            import urllib.parse
            query_params = urllib.parse.parse_qs(request.META.get('QUERY_STRING', ''))
            for key in ['tran_ref', 'tranRef', 'tranref', 'transaction_ref', 'transactionRef']:
                if key in query_params and query_params[key]:
                    tran_ref = query_params[key][0] if isinstance(query_params[key], list) else query_params[key]
                    break
        
        # Check nested structures (PayTabs might send data in nested format)
        if not tran_ref:
            for key_path in [
                ['transaction', 'tran_ref'],
                ['transaction', 'tranRef'],
                ['payment', 'tran_ref'],
                ['payment', 'tranRef'],
                ['data', 'tran_ref'],
                ['data', 'tranRef'],
            ]:
                current = payload
                try:
                    for key in key_path:
                        current = current.get(key, {})
                    if current and isinstance(current, str):
                        tran_ref = current
                        break
                except (AttributeError, TypeError):
                    continue

        logger.info(
            f"PayTabs return - Method: {request.method}, Payload: {payload}, Query: {dict(request.GET)}, POST: {dict(request.POST) if hasattr(request, 'POST') else {}}, Body: {request.body.decode('utf-8') if request.body else 'None'}"
        )
        
        # If tran_ref is missing, try to get it from the payment record using subscription_id
        if not tran_ref:
            subscription_id_param = request.GET.get("subscription_id") or payload.get("subscription_id")
            if subscription_id_param:
                try:
                    subscription_id = int(subscription_id_param)
                    # Find the most recent payment for this subscription
                    paytabs_gateway = PaymentGateway.objects.filter(
                        name__icontains="paytabs", enabled=True
                    ).first()
                    
                    if paytabs_gateway:
                        payment = Payment.objects.filter(
                            subscription_id=subscription_id,
                            payment_method=paytabs_gateway
                        ).order_by("-created_at").first()
                        
                        if payment and payment.tran_ref:
                            tran_ref = payment.tran_ref
                            logger.info(f"Found tran_ref from payment record: {tran_ref}")
                except (ValueError, Payment.DoesNotExist) as e:
                    logger.warning(f"Could not find payment record: {str(e)}")
        
        if not tran_ref:
            logger.error("Missing tran_ref in return URL - all data: %s", {
                "GET": dict(request.GET),
                "POST": dict(request.POST) if hasattr(request, 'POST') else {},
                "body": request.body.decode('utf-8') if request.body else None,
                "payload": payload
            })
            # Redirect to frontend with error status
            frontend_url = settings.FRONTEND_URL
            return redirect(f"{frontend_url}/payment/success?status=failed&message=Missing transaction reference")

        # Verify transaction (like uchat_paytabs_gateway)
        result = verify_paytabs_payment(tran_ref)
        logger.info(f"PayTabs verified result: {result}")

        # Extract subscription from cart_id
        cart_id = result.get("cart_id")
        if not cart_id:
            logger.error("Missing cart_id in verified result")
            frontend_url = settings.FRONTEND_URL
            return redirect(f"{frontend_url}/payment/success?status=failed&message=Invalid transaction")

        subscription_id = int(cart_id.replace("SUB-", ""))
        subscription = Subscription.objects.get(id=subscription_id)

        # Get subscription_id from URL params if available (for redirect)
        url_subscription_id = request.GET.get("subscription_id") or payload.get("subscription_id")
        if url_subscription_id:
            try:
                subscription_id = int(url_subscription_id)
            except:
                pass

        # Update payment status (like uchat_paytabs_gateway)
        payment_status = result.get("payment_result", {}).get("response_status")
        if payment_status == "A":  # Approved
            subscription.is_active = True
            subscription.save()

            # Mark company registration as completed
            company = subscription.company
            if company:
                company.registration_completed = True
                company.registration_completed_at = timezone.now()
                company.save()

            # Update or create payment record
            paytabs_gateway = PaymentGateway.objects.filter(
                name__icontains="paytabs", enabled=True
            ).first()

            if paytabs_gateway:
                amount = float(result.get("cart_amount", 0))
                # Find existing payment or create new one
                payment = Payment.objects.filter(
                    subscription=subscription,
                    payment_method=paytabs_gateway
                ).order_by("-created_at").first()
                
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

        # Redirect to frontend success page
        frontend_url = settings.FRONTEND_URL
        if payment_status == "A":
            return redirect(f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=success&tranRef={tran_ref}")
        else:
            return redirect(f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=failed&message=Payment failed")

    except Exception as e:
        logger.error(f"Error processing return: {str(e)}", exc_info=True)
        frontend_url = settings.FRONTEND_URL
        subscription_id = request.GET.get("subscription_id") or ""
        return redirect(f"{frontend_url}/payment/success?subscription_id={subscription_id}&status=error&message={str(e)}")


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
        logger.info(f"Checking payment status for subscription {subscription_id}, is_active: {subscription.is_active}")
        
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
            logger.info(f"Subscription {subscription_id} is ACTIVE - returning completed status immediately")
        elif payment:
            # If payment exists and has tran_ref, try to get paytabs_status
            if payment.tran_ref:
                try:
                    result = verify_paytabs_payment(payment.tran_ref)
                    paytabs_status = result.get("payment_result", {}).get("response_status")
                except Exception as e:
                    logger.warning(f"Could not verify payment with PayTabs: {str(e)}")
                    # If payment is completed in DB but verification fails, assume approved
                    if payment.payment_status == PaymentStatus.COMPLETED.value:
                        paytabs_status = "A"
            
            # If payment is completed in DB, ensure paytabs_status is set
            if payment.payment_status == PaymentStatus.COMPLETED.value and not paytabs_status:
                paytabs_status = "A"
        
        # Return all fields the frontend needs - ensure values match what frontend expects
        response_data = {
            "subscription_id": subscription_id,
            "subscription_active": bool(subscription.is_active),  # Ensure it's a boolean
            "payment_status": payment_status_value,  # Should be "completed" if done
            "paytabs_status": paytabs_status,  # Should be "A" if approved
        }
        
        logger.info(f"Payment status check for subscription {subscription_id}: subscription.is_active={subscription.is_active}, payment_status={payment_status_value}, paytabs_status={paytabs_status}, payment exists={payment is not None}")
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
