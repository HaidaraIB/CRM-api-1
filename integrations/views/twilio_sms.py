import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from crm_saas_api.responses import error_response, success_response, validation_error_response

from accounts.permissions import HasActiveSubscription
from ..decorators import rate_limit_webhook
from ..models import (
    IntegrationAccount, IntegrationLog, IntegrationPlatform,
    WhatsAppAccount, OAuthState, TwilioSettings,
    LeadSMSMessage, LeadWhatsAppMessage, MessageTemplate,
    SmsProvider,
)
from ..oauth_utils import get_oauth_handler, MetaOAuth
from ..serializers import (
    IntegrationAccountSerializer,
    IntegrationAccountCreateSerializer,
    IntegrationAccountUpdateSerializer,
    IntegrationAccountDetailSerializer,
    IntegrationLogSerializer,
    OAuthCallbackSerializer,
    TwilioSettingsSerializer,
    LeadSMSMessageSerializer,
    SendLeadSMSSerializer,
    LeadWhatsAppMessageSerializer,
    MessageTemplateSerializer,
)
from ..policy import (
    get_effective_integration_policy,
    get_plan_integration_access,
    is_any_sms_integration_allowed,
)
from settings.models import SystemSettings

logger = logging.getLogger(__name__)
from ..services.company_sms import send_company_sms


def _integration_gate(company, platform: str):
    plan_gate = get_plan_integration_access(company, platform)
    if not plan_gate["enabled"]:
        return error_response(plan_gate["message"], code="plan_integration_not_included", status_code=403)
    effective = get_effective_integration_policy(
        SystemSettings.get_settings().integration_policies or {},
        company_id=company.id,
        platform=platform,
    )
    if not effective["enabled"]:
        return error_response(effective["message"], code="integration_disabled", status_code=403)
    return None


def _sms_platform_for_settings(settings: TwilioSettings | None, request_data: dict | None = None) -> str:
    if request_data and request_data.get("provider"):
        return str(request_data["provider"]).strip() or SmsProvider.TWILIO
    if settings and settings.provider:
        return settings.provider
    return SmsProvider.TWILIO

@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def twilio_settings_view(request):
    """
    GET: إرجاع إعدادات Twilio للشركة (إن وُجدت).
    PUT: إنشاء أو تحديث إعدادات Twilio. نستخدم Twilio حصرياً لإرسال SMS.
    """
    company = request.user.company
    if request.method == 'GET':
        try:
            twilio_settings = TwilioSettings.objects.get(company=company)
        except TwilioSettings.DoesNotExist:
            twilio_settings = None
        if twilio_settings is None:
            if not is_any_sms_integration_allowed(company):
                blocked = _integration_gate(company, "twilio") or _integration_gate(company, "otpiq")
                if blocked is not None:
                    return blocked
        else:
            blocked = _integration_gate(company, _sms_platform_for_settings(twilio_settings))
            if blocked is not None:
                return blocked
        if twilio_settings is None:
            return success_response(
                data={
                    'provider': SmsProvider.TWILIO,
                    'account_sid': '',
                    'twilio_number': '',
                    'auth_token_masked': None,
                    'otpiq_api_key_masked': None,
                    'otpiq_route_provider': 'sms',
                    'sender_id': '',
                    'is_enabled': False,
                    'lead_created_sms_enabled': False,
                    'lead_created_sms_template': "Hello [first_name], we'll contact you soon!",
                },
            )
        serializer = TwilioSettingsSerializer(twilio_settings)
        return success_response(data=serializer.data)

    if request.method == 'PUT':
        twilio_settings, _ = TwilioSettings.objects.get_or_create(
            company=company,
            defaults={'is_enabled': False},
        )
        platform = _sms_platform_for_settings(twilio_settings, request.data)
        blocked = _integration_gate(company, platform)
        if blocked is not None:
            return blocked
        serializer = TwilioSettingsSerializer(twilio_settings, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save()
        return success_response(data=TwilioSettingsSerializer(twilio_settings).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def send_lead_sms_view(request):
    """
    إرسال رسالة SMS إلى رقم مرتبط بالليد عبر Twilio.
    يتم حفظ الرسالة في قاعدة البيانات وعرضها في تايملاين الليد.
    """
    from crm.models import Client
    serializer = SendLeadSMSSerializer(data=request.data)
    if not serializer.is_valid():
        return error_response(
            'Invalid request. Check the message and phone number.',
            code='sms_error_validation',
            details=serializer.errors,
        )
    data = serializer.validated_data
    lead_id = data['lead_id']
    phone_number = data['phone_number']
    body = data['body']

    company = request.user.company
    # Plan gating: monthly usage only (integration access handled by integration gate).
    from subscriptions.entitlements import require_monthly_usage, increment_monthly_usage
    require_monthly_usage(
        company,
        "monthly_sms_messages",
        requested_delta=1,
        message="You have reached your monthly SMS limit. Please upgrade your plan.",
        error_key="plan_usage_monthly_sms_exceeded",
    )
    try:
        client = Client.objects.get(id=lead_id, company=company)
    except Client.DoesNotExist:
        return error_response(
            'Lead not found or access denied.',
            code='sms_error_lead_not_found',
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        twilio_settings = TwilioSettings.objects.get(company=company, is_enabled=True)
    except TwilioSettings.DoesNotExist:
        return error_response(
            'SMS is not configured or not enabled. Set it up in Integrations.',
            code='sms_error_not_configured',
        )

    blocked = _integration_gate(company, twilio_settings.provider or SmsProvider.TWILIO)
    if blocked is not None:
        return blocked

    ok, external_id, error_key, error_msg, provider_used = send_company_sms(
        twilio_settings,
        to_phone=phone_number,
        body=body,
    )
    if not ok:
        logger.warning("SMS send failed provider=%s key=%s", provider_used, error_key)
        return error_response(
            error_msg or 'SMS request was rejected. Please check your settings.',
            code=error_key or 'sms_error_send_failed',
            status_code=status.HTTP_502_BAD_GATEWAY if error_key == 'sms_error_send_failed' else status.HTTP_400_BAD_REQUEST,
        )

    twilio_sid = external_id if provider_used == SmsProvider.TWILIO else None
    sms_record = LeadSMSMessage.objects.create(
        client=client,
        phone_number=phone_number,
        body=body,
        direction=LeadSMSMessage.DIRECTION_OUTBOUND,
        provider=provider_used,
        external_message_id=external_id,
        twilio_sid=twilio_sid,
        created_by=request.user,
    )
    # Increment usage after success only
    increment_monthly_usage(company, "monthly_sms_messages", requested_delta=1)
    return success_response(
        data=LeadSMSMessageSerializer(sms_record).data,
        status_code=status.HTTP_201_CREATED,
    )


class LeadSMSMessageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    قائمة رسائل SMS للعميل المحتمل. للعرض في التايملاين.
    GET /api/integrations/sms/?client=:client_id
    """
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    serializer_class = LeadSMSMessageSerializer

    def get_queryset(self):
        user = self.request.user
        qs = LeadSMSMessage.objects.filter(client__company=user.company).order_by('-created_at')
        client_id = self.request.query_params.get('client')
        if client_id and str(client_id).isdigit():
            qs = qs.filter(client_id=client_id)
        return qs


class LeadWhatsAppMessageViewSet(viewsets.ReadOnlyModelViewSet):
    """
    قائمة رسائل واتساب للعميل. للعرض في التايملاين ومركز المراسلات.
    GET /api/integrations/whatsapp/messages/?client=:client_id
    """
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    serializer_class = LeadWhatsAppMessageSerializer

    def get_queryset(self):
        user = self.request.user
        qs = LeadWhatsAppMessage.objects.filter(client__company=user.company).order_by('-created_at')
        client_id = self.request.query_params.get('client')
        if client_id and str(client_id).isdigit():
            qs = qs.filter(client_id=client_id)
        return qs


