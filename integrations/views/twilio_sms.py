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

logger = logging.getLogger(__name__)
from ..services.twilio_text import strip_ansi, twilio_error_to_key

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
            return success_response(
                data={
                    'account_sid': '',
                    'twilio_number': '',
                    'auth_token_masked': None,
                    'sender_id': '',
                    'is_enabled': False,
                },
            )
        serializer = TwilioSettingsSerializer(twilio_settings)
        return success_response(data=serializer.data)

    if request.method == 'PUT':
        twilio_settings, _ = TwilioSettings.objects.get_or_create(
            company=company,
            defaults={'is_enabled': False},
        )
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
    # Plan gating: feature + monthly usage
    from subscriptions.entitlements import require_feature, require_monthly_usage, increment_monthly_usage
    require_feature(
        company,
        "sms_enabled",
        message="SMS is not available in your current plan. Please upgrade your plan.",
        error_key="plan_feature_sms_disabled",
    )
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

    account_sid = twilio_settings.account_sid
    auth_token = twilio_settings.get_auth_token()
    twilio_number = twilio_settings.twilio_number
    sender_id = (twilio_settings.sender_id or '').strip()
    # Prefer Sender ID (alphanumeric) when set; otherwise use Twilio number
    from_value = sender_id if sender_id else (twilio_number or '')
    if not account_sid or not auth_token or not from_value:
        return error_response(
            'Account SID, Auth Token, and either Sender ID or sender number are required.',
            code='sms_error_credentials_incomplete',
        )

    try:
        from twilio.rest import Client as TwilioClient
        from twilio.base.exceptions import TwilioRestException
        twilio_client = TwilioClient(account_sid, auth_token)
        # Normalize phone to E.164 (same as Digital Marketing Manager: 07... -> +964..., then ensure +)
        to = phone_number.strip().replace(' ', '').replace('-', '')
        if to.startswith('07') and len(to) >= 10:
            to = '+964' + to[1:]
        elif not to.startswith('+'):
            to = '+' + to
        message = twilio_client.messages.create(
            body=body,
            from_=from_value,
            to=to,
        )
        twilio_sid = message.sid
    except TwilioRestException as e:
        logger.warning("Twilio API error (code=%s): %s", getattr(e, 'code', None), getattr(e, 'msg', str(e)))
        error_key = twilio_error_to_key(e)
        clean_msg = strip_ansi(getattr(e, 'msg', None) or str(e))
        if clean_msg and len(clean_msg) > 400:
            clean_msg = clean_msg.split('\n')[0]
        return error_response(
            clean_msg or 'SMS request was rejected. Please check your settings.',
            code=error_key,
        )
    except Exception as e:
        logger.exception("Twilio send SMS failed")
        clean_msg = strip_ansi(str(e))
        if len(clean_msg) > 400:
            clean_msg = clean_msg.split('\n')[0]
        return error_response(
            clean_msg or 'Failed to send SMS. Please try again later.',
            code='sms_error_send_failed',
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    sms_record = LeadSMSMessage.objects.create(
        client=client,
        phone_number=phone_number,
        body=body,
        direction=LeadSMSMessage.DIRECTION_OUTBOUND,
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
        if client_id:
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
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs


