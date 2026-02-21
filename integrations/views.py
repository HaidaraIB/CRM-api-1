import requests
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from accounts.permissions import HasActiveSubscription
from .models import IntegrationAccount, IntegrationLog, IntegrationPlatform, WhatsAppAccount, TwilioSettings, LeadSMSMessage, MessageTemplate
from .serializers import (
    IntegrationAccountSerializer,
    IntegrationAccountCreateSerializer,
    IntegrationAccountUpdateSerializer,
    IntegrationAccountDetailSerializer,
    IntegrationLogSerializer,
    OAuthCallbackSerializer,
    TwilioSettingsSerializer,
    LeadSMSMessageSerializer,
    SendLeadSMSSerializer,
    MessageTemplateSerializer,
)
from .oauth_utils import get_oauth_handler, MetaOAuth
from .decorators import rate_limit_webhook
import json
import re
import hmac
import hashlib
import logging

logger = logging.getLogger(__name__)

# Strip ANSI escape sequences for clean user-facing error messages
def _strip_ansi(text):
    if not text or not isinstance(text, str):
        return text or ""
    return re.sub(r'\x1b\[[0-9;]*m', '', text).strip()


# Map Twilio error codes and message substrings to frontend error_key (for localization)
def _twilio_error_to_key(e):
    code = getattr(e, 'code', None)
    msg = (getattr(e, 'msg', None) or str(e)).lower()
    if code == 21606 or code == 21608 or ("from" in msg and "not a valid" in msg):
        return 'sms_error_invalid_from_number'
    if code == 21211 or "invalid" in msg and ("to" in msg or "recipient" in msg):
        return 'sms_error_invalid_to_number'
    if code == 20003 or "authentic" in msg or "credentials" in msg or "unauthorized" in msg:
        return 'sms_error_auth'
    if code == 90010 or "inactive" in msg:
        return 'sms_error_account_inactive'
    if code == 20429 or "too many" in msg or "rate" in msg:
        return 'sms_error_rate_limit'
    if code == 21614 or "not a valid mobile" in msg:
        return 'sms_error_invalid_mobile'
    if code == 21408 or "permission" in msg or "unverified" in msg:
        return 'sms_error_permission'
    if "blacklisted" in msg or "blocked" in msg:
        return 'sms_error_blocked'
    return 'sms_error_twilio_rejected'
import logging

logger = logging.getLogger(__name__)


class IntegrationAccountViewSet(viewsets.ModelViewSet):
    """
    ViewSet لإدارة حسابات التكامل
    
    العمليات المتاحة:
    - GET /api/integrations/accounts/ - قائمة الحسابات
    - GET /api/integrations/accounts/{id}/ - تفاصيل حساب
    - POST /api/integrations/accounts/ - إنشاء حساب جديد
    - PUT /api/integrations/accounts/{id}/ - تحديث حساب
    - DELETE /api/integrations/accounts/{id}/ - حذف حساب
    - POST /api/integrations/accounts/{id}/connect/ - ربط حساب (OAuth)
    - POST /api/integrations/accounts/{id}/disconnect/ - قطع الاتصال
    - POST /api/integrations/accounts/{id}/sync/ - مزامنة البيانات
    """
    
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    
    def get_queryset(self):
        """الحصول على حسابات الشركة فقط"""
        user = self.request.user
        queryset = IntegrationAccount.objects.filter(company=user.company)
        
        # فلترة حسب المنصة
        platform = self.request.query_params.get('platform', None)
        if platform:
            queryset = queryset.filter(platform=platform)
        
        # فلترة حسب الحالة
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """اختيار Serializer حسب العملية"""
        if self.action == 'create':
            return IntegrationAccountCreateSerializer
        elif self.action == 'update' or self.action == 'partial_update':
            return IntegrationAccountUpdateSerializer
        elif self.action == 'retrieve':
            return IntegrationAccountDetailSerializer
        return IntegrationAccountSerializer
    
    def perform_create(self, serializer):
        """إنشاء حساب تكامل جديد"""
        serializer.save(
            company=self.request.user.company,
            created_by=self.request.user,
        )
    
    @action(detail=False, methods=['get'])
    def platforms(self, request):
        """الحصول على قائمة المنصات المدعومة (OAuth). TikTok = Lead Gen فقط فلا يظهر في إضافة حساب."""
        platforms = [
            {'value': c[0], 'label': c[1]}
            for c in IntegrationPlatform.choices
            if c[0] != 'tiktok'
        ]
        return Response(platforms)
    
    @action(detail=True, methods=['post'])
    def connect(self, request, pk=None):
        """بدء عملية OAuth لربط الحساب (Meta / WhatsApp). TikTok = Lead Gen فقط."""
        account = self.get_object()
        if account.platform == 'tiktok':
            return Response(
                {'error': 'TikTok is Lead Gen only. Use the webhook URL in Integrations → TikTok.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            oauth_handler = get_oauth_handler(account.platform)
            state = oauth_handler.generate_state()
            request.session[f'oauth_state_{account.id}'] = state
            request.session[f'oauth_account_id_{state}'] = account.id
            auth_url = oauth_handler.get_authorization_url(state)
            return Response({'authorization_url': auth_url, 'state': state})
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get', 'post'], url_path='oauth/callback/(?P<platform>[^/]+)', permission_classes=[AllowAny])
    def oauth_callback(self, request, platform):
        """
        معالجة OAuth Callback من المنصة
        
        هذا endpoint يتم استدعاؤه من المنصة بعد موافقة المستخدم
        لا يتطلب authentication لأنه يأتي من منصة خارجية
        """
        # Facebook يرسل code و state في query parameters (GET request)
        # التحقق من وجود code في الطلب
        if 'code' not in request.query_params and 'code' not in request.data:
            return Response(
                {
                    'error': 'Missing authorization code',
                    'detail': 'This endpoint is called by Facebook after user authorization. Please complete the OAuth flow by clicking "Connect" button in the integrations page.',
                    'hint': 'The authorization code should be provided by Facebook in the callback URL.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = OAuthCallbackSerializer(data=request.query_params or request.data)
        
        if not serializer.is_valid():
            return Response(
                {
                    'error': 'Invalid callback parameters',
                    'details': serializer.errors,
                    'hint': 'This endpoint expects code and state parameters from Facebook OAuth callback.'
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        code = serializer.validated_data.get('code')
        state = serializer.validated_data.get('state')
        error = serializer.validated_data.get('error')
        
        if error:
            return Response(
                {'error': error, 'description': serializer.validated_data.get('error_description')},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # التحقق من state
        account_id = request.session.get(f'oauth_account_id_{state}')
        if not account_id:
            return Response(
                {'error': 'Invalid state'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # الحصول على account من session data (لا نحتاج request.user)
            account = IntegrationAccount.objects.get(id=account_id)
        except IntegrationAccount.DoesNotExist:
            return Response(
                {'error': 'Account not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            oauth_handler = get_oauth_handler(account.platform)
            token_data = oauth_handler.exchange_code_for_token(code)
            user_info = oauth_handler.get_user_info(token_data['access_token'])
            account.set_access_token(token_data['access_token'])
            if 'refresh_token' in token_data:
                account.set_refresh_token(token_data['refresh_token'])
            expires_in = token_data.get('expires_in', 0)
            if expires_in:
                account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
            account.external_account_id = user_info.get('id') or user_info.get('open_id')
            account.external_account_name = user_info.get('name') or user_info.get('display_name')
            account.status = 'connected'
            account.error_message = None
            account.metadata = {
                'user_info': user_info,
                'token_type': token_data.get('token_type', 'Bearer'),
            }
            # للحصول على الصفحات (Meta)
            if account.platform == 'meta' and hasattr(oauth_handler, 'get_pages'):
                try:
                    pages = oauth_handler.get_pages(token_data['access_token'])
                    account.metadata['pages'] = pages
                    # حفظ Page Access Tokens في metadata
                    for page in pages:
                        page['access_token'] = page.get('access_token', '')
                except Exception as e:
                    # لا نوقف العملية إذا فشل الحصول على الصفحات
                    pass
            
            # للحصول على WhatsApp: WABA + Phone Number IDs وحفظها في جدول WhatsAppAccount
            if account.platform == 'whatsapp':
                try:
                    oauth_handler = get_oauth_handler('whatsapp')
                    if hasattr(oauth_handler, 'get_waba_and_phone_numbers'):
                        waba_list = oauth_handler.get_waba_and_phone_numbers(token_data['access_token'])
                        for item in waba_list:
                            waba_id = item.get('waba_id')
                            business_id = item.get('business_id')
                            for ph in item.get('phone_numbers') or []:
                                phone_number_id = ph.get('id')
                                if not phone_number_id:
                                    continue
                                display = (ph.get('display_phone_number') or '').strip()
                                wa_account, created = WhatsAppAccount.objects.update_or_create(
                                    phone_number_id=phone_number_id,
                                    defaults={
                                        'company': account.company,
                                        'waba_id': waba_id,
                                        'business_id': business_id or '',
                                        'display_phone_number': display or None,
                                        'status': 'connected',
                                        'integration_account': account,
                                    },
                                )
                                wa_account.set_access_token(token_data['access_token'])
                                wa_account.save()
                        if waba_list:
                            first_waba = waba_list[0]
                            first_phones = first_waba.get('phone_numbers') or []
                            if first_phones:
                                account.metadata['waba_id'] = first_waba.get('waba_id')
                                account.metadata['phone_number_id'] = first_phones[0].get('id')
                                account.phone_number = first_phones[0].get('display_phone_number')
                except Exception as e:
                    logger.warning("WhatsApp WABA/phone fetch failed: %s", e)
            
            account.save()
            
            # تسجيل العملية
            IntegrationLog.objects.create(
                account=account,
                action='oauth_connect',
                status='success',
                message='Account connected successfully',
            )
            
            request.session.pop(f'oauth_state_{account.id}', None)
            request.session.pop(f'oauth_account_id_{state}', None)
            # إعادة التوجيه إلى صفحة النجاح في Frontend
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            return redirect(f"{frontend_url}/integrations?connected=true&account_id={account.id}")
            
        except Exception as e:
            account.status = 'error'
            account.error_message = str(e)
            account.save()
            
            IntegrationLog.objects.create(
                account=account,
                action='oauth_connect',
                status='error',
                message='Failed to connect account',
                error_details=str(e),
            )
            
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def disconnect(self, request, pk=None):
        """قطع الاتصال مع الحساب"""
        account = self.get_object()
        
        account.set_access_token(None)
        account.set_refresh_token(None)
        account.token_expires_at = None
        account.status = 'disconnected'
        account.save()
        
        IntegrationLog.objects.create(
            account=account,
            action='disconnect',
            status='success',
            message='Account disconnected',
        )
        
        return Response({'message': 'Account disconnected successfully'})
    
    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """مزامنة البيانات مع المنصة"""
        account = self.get_object()
        
        if account.status != 'connected':
            return Response(
                {'error': 'Account is not connected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if account.is_token_expired():
            # محاولة تجديد Token
            try:
                oauth_handler = get_oauth_handler(account.platform)
                refresh_token = account.get_refresh_token()
                if refresh_token:
                    token_data = oauth_handler.refresh_token(refresh_token)
                    account.set_access_token(token_data['access_token'])
                    if 'refresh_token' in token_data:
                        account.set_refresh_token(token_data['refresh_token'])
                    expires_in = token_data.get('expires_in', 0)
                    if expires_in:
                        account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
                    account.save()
                else:
                    return Response(
                        {'error': 'Token expired and no refresh token available'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                account.status = 'expired'
                account.error_message = str(e)
                account.save()
                return Response(
                    {'error': f'Failed to refresh token: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # مزامنة حسب المنصة (TikTok = Lead Gen فقط، لا sync OAuth)
        account.last_sync_at = timezone.now()
        account.save()
        
        IntegrationLog.objects.create(
            account=account,
            action='sync',
            status='success',
            message='Data synced successfully',
        )
        
        return Response({'message': 'Sync completed successfully'})
    
    @action(detail=False, methods=['get'], url_path='tiktok-leadgen-config')
    def tiktok_leadgen_config(self, request):
        """
        TikTok Lead Gen فقط: إرجاع رابط الويب هوك لهذه الشركة لتسجيله في TikTok Ads Manager.
        GET /api/integrations/accounts/tiktok-leadgen-config/
        """
        company = request.user.company
        base = getattr(settings, 'API_BASE_URL', '').rstrip('/')
        webhook_url = f"{base}/api/integrations/webhooks/tiktok-leadgen/?company_id={company.id}"
        return Response({
            'webhook_url': webhook_url,
            'company_id': company.id,
        })
    
    @action(detail=True, methods=['get'])
    def lead_forms(self, request, pk=None):
        """
        الحصول على قائمة Lead Forms من صفحة Meta معينة
        
        GET /api/integrations/accounts/{id}/lead_forms/?page_id={page_id}
        """
        account = self.get_object()
        
        if account.platform != 'meta':
            return Response(
                {'error': 'This endpoint is only available for Meta accounts'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if account.status != 'connected':
            return Response(
                {'error': 'Account is not connected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        page_id = request.query_params.get('page_id')
        if not page_id:
            return Response(
                {'error': 'page_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            meta_oauth = MetaOAuth()
            
            # الحصول على Page Access Token
            pages = account.metadata.get('pages', [])
            page_access_token = None
            for page in pages:
                if page.get('id') == page_id:
                    page_access_token = page.get('access_token')
                    break
            
            if not page_access_token:
                # محاولة الحصول على Page Access Token من API
                access_token = account.get_access_token()
                if not access_token:
                    return Response(
                        {'error': 'No access token available'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                page_access_token = meta_oauth.get_page_access_token(
                    page_id,
                    access_token
                )
            
            # جلب Lead Forms
            lead_forms = meta_oauth.get_lead_forms(page_id, page_access_token)
            
            return Response({
                'page_id': page_id,
                'lead_forms': lead_forms,
            })
            
        except Exception as e:
            logger.error(f"Error fetching lead forms: {str(e)}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def select_lead_form(self, request, pk=None):
        """
        ربط Lead Form معين بكامبين
        
        POST /api/integrations/accounts/{id}/select_lead_form/
        Body: {
            "page_id": "123456789",
            "form_id": "987654321",
            "campaign_id": 1  # optional
        }
        """
        account = self.get_object()
        
        if account.platform != 'meta':
            return Response(
                {'error': 'This endpoint is only available for Meta accounts'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        page_id = request.data.get('page_id')
        form_id = request.data.get('form_id')
        campaign_id = request.data.get('campaign_id')
        
        if not page_id or not form_id:
            return Response(
                {'error': 'page_id and form_id are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # التحقق من وجود الكامبين إذا تم توفيره
        campaign = None
        if campaign_id:
            from crm.models import Campaign
            try:
                campaign = Campaign.objects.get(id=campaign_id, company=account.company)
            except Campaign.DoesNotExist:
                return Response(
                    {'error': 'Campaign not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # تحديث metadata
        if 'form_campaign_mapping' not in account.metadata:
            account.metadata['form_campaign_mapping'] = {}
        
        if campaign_id:
            account.metadata['form_campaign_mapping'][form_id] = campaign_id
        else:
            # إزالة الربط إذا لم يتم توفير campaign_id
            account.metadata['form_campaign_mapping'].pop(form_id, None)
        
        account.metadata['selected_page_id'] = page_id
        account.metadata['selected_form_id'] = form_id
        account.save()
        
        IntegrationLog.objects.create(
            account=account,
            action='select_lead_form',
            status='success',
            message=f'Lead form {form_id} selected for page {page_id}',
            response_data={
                'page_id': page_id,
                'form_id': form_id,
                'campaign_id': campaign_id,
            },
        )
        
        return Response({
            'message': 'Lead form selected successfully',
            'page_id': page_id,
            'form_id': form_id,
            'campaign_id': campaign_id,
        })


class IntegrationLogViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet لعرض سجلات التكامل فقط (قراءة)"""
    
    serializer_class = IntegrationLogSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    
    def get_queryset(self):
        """الحصول على سجلات حسابات الشركة فقط"""
        user = self.request.user
        account_id = self.request.query_params.get('account', None)
        
        queryset = IntegrationLog.objects.filter(
            account__company=user.company
        )
        
        if account_id:
            queryset = queryset.filter(account_id=account_id)
        
        return queryset.order_by('-created_at')


# ==================== WhatsApp Send Message ====================
# إرسال رسالة واتساب: POST إلى Graph API باستخدام phone_number_id و Access Token الخاص بالـ tenant

@api_view(['POST'])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_send_message(request):
    """
    إرسال رسالة واتساب من رقم الشركة المتصل.
    POST /api/integrations/whatsapp/send/
    Body: { "phone_number_id": "optional", "to": "971501234567", "message": "نص الرسالة" }
    """
    company = request.user.company
    phone_number_id = request.data.get('phone_number_id')
    to = request.data.get('to')
    message = request.data.get('message') or request.data.get('text')
    if not to or not message:
        return Response(
            {'error': 'to and message are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    # تطبيع رقم المستلم (إزالة + وفراغات)
    to = str(to).replace(' ', '').replace('+', '').strip()
    if not to.isdigit():
        return Response(
            {'error': 'Invalid "to" phone number'},
            status=status.HTTP_400_BAD_REQUEST
        )
    qs = WhatsAppAccount.objects.filter(company=company, status='connected')
    if phone_number_id:
        qs = qs.filter(phone_number_id=phone_number_id)
    wa_account = qs.first()
    if not wa_account:
        return Response(
            {'error': 'No connected WhatsApp number for this company'},
            status=status.HTTP_404_NOT_FOUND
        )
    access_token = wa_account.get_access_token()
    if not access_token:
        return Response(
            {'error': 'WhatsApp account has no access token'},
            status=status.HTTP_400_BAD_REQUEST
        )
    url = f"https://graph.facebook.com/v18.0/{wa_account.phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": message[:4096]},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if wa_account.integration_account_id:
            IntegrationLog.objects.create(
                account_id=wa_account.integration_account_id,
                action='whatsapp_message_sent',
                status='success',
                message=f'Message sent to {to}',
                response_data=data,
            )
        return Response(data, status=status.HTTP_200_OK)
    except requests.RequestException as e:
        err_body = {'error': str(e)}
        if getattr(e, 'response', None) is not None:
            r = e.response
            try:
                err_body = r.json()
            except Exception:
                err_body = {'error': getattr(r, 'text', str(r))}
        logger.warning("WhatsApp send failed: %s", err_body)
        return Response(err_body, status=status.HTTP_400_BAD_REQUEST)


# ==================== TikTok Lead Gen Webhook ====================
# استقبال ليدز من TikTok Instant Form (TikTok Marketing API / Leads Center).
# يُسجّل في TikTok Ads Manager → Leads Center → CRM integration → Custom API with Webhooks.


def _parse_tiktok_leadgen_payload(body):
    """
    استخراج lead_id, advertiser_id, name, phone, email من payload TikTok Lead Gen.
    يدعم أشكالاً متعددة حسب وثائق TikTok Marketing API / شركاء التكامل.
    """
    if not body:
        return None
    # دعم content كـ JSON string (أحياناً يُرسل هكذا)
    data = body.get('data') or body
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    content_str = body.get('content')
    if content_str and isinstance(content_str, str):
        try:
            data = json.loads(content_str)
        except json.JSONDecodeError:
            pass
    if not isinstance(data, dict):
        return None
    lead_id = data.get('lead_id') or data.get('id') or data.get('leadId')
    form_id = data.get('form_id') or data.get('formId')
    advertiser_id = str(data.get('advertiser_id') or data.get('advertiserId') or '')
    # استخراج الاسم والهاتف والبريد من list من نوع [{"key":"Full name","value":"x"}] أو من dict
    name = None
    phone = None
    email = None
    lead_list = data.get('list') or data.get('answers') or data.get('field_data') or []
    if isinstance(lead_list, list):
        key_map = {}
        for item in lead_list:
            if isinstance(item, dict):
                k = (item.get('key') or item.get('name') or item.get('label') or '').lower()
                v = item.get('value') or item.get('values')
                if isinstance(v, list):
                    v = v[0] if v else ''
                if k and v:
                    key_map[k] = str(v).strip()
        name = (key_map.get('full name') or key_map.get('full_name') or key_map.get('name') or
                key_map.get('contact name') or key_map.get('contact_name') or '')
        phone = (key_map.get('phone') or key_map.get('phone_number') or key_map.get('mobile') or
                 key_map.get('tel') or key_map.get('contact phone') or '')
        email = (key_map.get('email') or key_map.get('email_address') or key_map.get('e-mail') or '')
    if isinstance(data.get('answers'), dict):
        ans = data['answers']
        name = name or ans.get('full_name') or ans.get('name') or ans.get('full name') or ''
        phone = phone or ans.get('phone') or ans.get('phone_number') or ans.get('mobile') or ''
        email = email or ans.get('email') or ans.get('email_address') or ''
    name = name or data.get('full_name') or data.get('name') or data.get('full name') or ''
    phone = phone or data.get('phone') or data.get('phone_number') or data.get('mobile') or ''
    email = email or data.get('email') or data.get('email_address') or ''
    if not lead_id and not name and not phone and not email:
        return None
    return {
        'lead_id': str(lead_id) if lead_id else None,
        'form_id': str(form_id) if form_id else None,
        'advertiser_id': advertiser_id or None,
        'name': (name or 'TikTok Lead').strip(),
        'phone': (phone or '').strip(),
        'email': (email or '').strip(),
        'raw': data,
    }


def _get_company_id_for_tiktok_leadgen(advertiser_id, request):
    """تحديد company_id من إعدادات التطبيق أو من query param."""
    company_id = request.GET.get('company_id')
    if company_id:
        try:
            return int(company_id)
        except (ValueError, TypeError):
            pass
    mapping = getattr(settings, 'TIKTOK_LEADGEN_ADVERTISER_MAPPING', '{}')
    if isinstance(mapping, str):
        try:
            mapping = json.loads(mapping) if mapping else {}
        except json.JSONDecodeError:
            mapping = {}
    if advertiser_id and mapping:
        cid = mapping.get(str(advertiser_id)) or mapping.get(advertiser_id)
        if cid is not None:
            try:
                return int(cid)
            except (ValueError, TypeError):
                pass
    default = getattr(settings, 'TIKTOK_LEADGEN_COMPANY_ID', '')
    if default:
        try:
            return int(default)
        except (ValueError, TypeError):
            pass
    return None


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit_webhook(max_requests=300, window=60)
def tiktok_leadgen_webhook(request):
    """
    Webhook لاستقبال ليدز TikTok Lead Generation (Instant Form).
    URL للتسجيل في TikTok Ads Manager: {API_BASE_URL}/api/integrations/webhooks/tiktok-leadgen/
    أو مع company_id: .../tiktok-leadgen/?company_id=1
    يرد 200 دائماً. يُنشئ Client في الـ CRM ويسجّل في IntegrationLog.
    """
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    # الرد 200 فوراً حتى لا تعيد TikTok المحاولة
    payload = _parse_tiktok_leadgen_payload(body)
    if not payload:
        logger.warning("TikTok Lead Gen webhook: could not parse payload: %s", json.dumps(body)[:300])
        return HttpResponse('OK', status=200)
    company_id = _get_company_id_for_tiktok_leadgen(payload.get('advertiser_id'), request)
    if not company_id:
        logger.warning("TikTok Lead Gen webhook: no company_id (set TIKTOK_LEADGEN_COMPANY_ID or ?company_id= or ADVERTISER_MAPPING)")
        return HttpResponse('OK', status=200)
    from companies.models import Company
    try:
        company = Company.objects.get(id=company_id)
    except Company.DoesNotExist:
        logger.warning("TikTok Lead Gen webhook: company_id=%s not found", company_id)
        return HttpResponse('OK', status=200)
    from .models import IntegrationAccount, IntegrationLog
    from crm.models import Client, ClientPhoneNumber, ClientEvent
    from crm.signals import get_least_busy_employee
    account, _ = IntegrationAccount.objects.get_or_create(
        company=company,
        platform='tiktok',
        external_account_id='leadgen_%s' % company_id,
        defaults={
            'name': 'TikTok Lead Gen',
            'status': 'connected',
        },
    )
    lead_id = payload.get('lead_id')
    if lead_id:
        if IntegrationLog.objects.filter(
            account=account,
            action='tiktok_lead_received',
            response_data__lead_id=lead_id,
        ).exists():
            logger.info("TikTok Lead Gen: duplicate lead_id=%s ignored", lead_id)
            return HttpResponse('OK', status=200)
    try:
        client = Client.objects.create(
            name=payload['name'],
            priority='medium',
            type='fresh',
            company=company,
            source='tiktok',
            integration_account=account,
            phone_number=payload.get('phone') or None,
        )
        if payload.get('phone'):
            ClientPhoneNumber.objects.create(
                client=client,
                phone_number=payload['phone'],
                phone_type='mobile',
                is_primary=True,
            )
        if company.auto_assign_enabled:
            employee = get_least_busy_employee(company)
            if employee:
                client.assigned_to = employee
                client.assigned_at = timezone.now()
                client.save()
                ClientEvent.objects.create(
                    client=client,
                    event_type='assignment',
                    old_value='Unassigned',
                    new_value=employee.get_full_name() or employee.username,
                    notes='Auto-assigned from TikTok Lead Gen',
                )
        created_notes = 'Lead from TikTok Instant Form (form_id=%s)' % (payload.get('form_id') or '')
        if payload.get('email'):
            created_notes += '. Email: %s' % payload['email']
        ClientEvent.objects.create(
            client=client,
            event_type='created',
            new_value='TikTok Lead Gen',
            notes=created_notes,
        )
        IntegrationLog.objects.create(
            account=account,
            action='tiktok_lead_received',
            status='success',
            message='Lead created: %s' % payload['name'],
            response_data={
                'lead_id': lead_id,
                'form_id': payload.get('form_id'),
                'advertiser_id': payload.get('advertiser_id'),
                'client_id': client.id,
            },
        )
        logger.info("TikTok Lead Gen: created client id=%s for company_id=%s", client.id, company_id)
    except Exception as e:
        logger.exception("TikTok Lead Gen webhook: failed to create client: %s", e)
        IntegrationLog.objects.create(
            account=account,
            action='tiktok_lead_received',
            status='error',
            message='Failed to create client',
            error_details=str(e),
            response_data={'lead_id': lead_id, 'payload_keys': list(payload.keys())},
        )
    return HttpResponse('OK', status=200)


# ==================== Webhook Views ====================

def verify_meta_webhook_signature(request):
    """التحقق من توقيع Meta Webhook"""
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature:
        return False
    
    # استخراج التوقيع من Header
    # Format: sha256=<signature>
    if not signature.startswith('sha256='):
        return False
    
    received_signature = signature[7:]  # Remove 'sha256=' prefix
    
    # حساب التوقيع المتوقع
    app_secret = getattr(settings, 'META_CLIENT_SECRET', '')
    if not app_secret:
        logger.warning("META_CLIENT_SECRET not set in settings")
        return False
    
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        request.body,
        hashlib.sha256
    ).hexdigest()
    
    # مقارنة التوقيعات بشكل آمن
    return hmac.compare_digest(received_signature, expected_signature)


@csrf_exempt
@require_http_methods(["GET", "POST"])
@rate_limit_webhook(max_requests=100, window=60)  # 100 requests per minute
def meta_webhook(request):
    """
    Webhook endpoint لاستقبال الليدز من Meta Lead Forms
    
    GET: للتحقق من Webhook (Meta Challenge)
    POST: لاستقبال الليدز الجديدة
    """
    if request.method == 'GET':
        # Meta Webhook Verification
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        verify_token = getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')
        
        if mode == 'subscribe' and token == verify_token:
            logger.info("Meta webhook verified successfully")
            return HttpResponse(challenge, content_type='text/plain')
        else:
            logger.warning(f"Meta webhook verification failed: mode={mode}, token_match={token == verify_token}")
            return HttpResponse('Forbidden', status=403)
    
    # POST: استقبال الليدز
    if request.method == 'POST':
        # التحقق من التوقيع
        if not verify_meta_webhook_signature(request):
            logger.warning("Meta webhook signature verification failed")
            return HttpResponse('Unauthorized', status=401)
        
        try:
            payload = json.loads(request.body)
            logger.info(f"Meta webhook received: {json.dumps(payload, indent=2)}")
            
            # Meta يرسل البيانات في entry[0].changes[0].value
            entry = payload.get('entry', [])
            if not entry:
                logger.warning("No entry in Meta webhook payload")
                return JsonResponse({'status': 'ok'}, status=200)
            
            for entry_item in entry:
                changes = entry_item.get('changes', [])
                for change in changes:
                    if change.get('field') == 'leadgen':
                        value = change.get('value', {})
                        leadgen_id = value.get('leadgen_id')
                        form_id = value.get('form_id')
                        page_id = value.get('page_id')
                        
                        if not leadgen_id or not form_id or not page_id:
                            logger.warning(f"Missing required fields in webhook: leadgen_id={leadgen_id}, form_id={form_id}, page_id={page_id}")
                            continue
                        
                        # البحث عن IntegrationAccount المرتبط بهذا form_id أو page_id
                        try:
                            account = None
                            
                            # الطريقة 1: البحث عن form_id في metadata (الأكثر دقة)
                            accounts_with_form = IntegrationAccount.objects.filter(
                                platform='meta',
                                status='connected',
                                metadata__contains={'selected_form_id': form_id}
                            )
                            
                            if accounts_with_form.exists():
                                # إذا وجدنا أكثر من حساب (نادر جداً)، نأخذ الأول
                                account = accounts_with_form.first()
                                logger.info(f"Found account by form_id: {form_id} -> Company: {account.company.name}")
                            
                            # الطريقة 2: إذا لم نجد، نبحث عن page_id في metadata
                            if not account:
                                accounts_with_page = IntegrationAccount.objects.filter(
                                    platform='meta',
                                    status='connected',
                                    metadata__contains={'selected_page_id': page_id}
                                )
                                
                                if accounts_with_page.exists():
                                    account = accounts_with_page.first()
                                    logger.info(f"Found account by page_id: {page_id} -> Company: {account.company.name}")
                            
                            # الطريقة 3: البحث في pages array داخل metadata
                            if not account:
                                # البحث في جميع IntegrationAccounts المرتبطة بـ Meta
                                all_meta_accounts = IntegrationAccount.objects.filter(
                                    platform='meta',
                                    status='connected'
                                )
                                
                                for acc in all_meta_accounts:
                                    pages = acc.metadata.get('pages', [])
                                    for page in pages:
                                        if page.get('id') == page_id:
                                            account = acc
                                            logger.info(f"Found account by page_id in pages array: {page_id} -> Company: {account.company.name}")
                                            break
                                    if account:
                                        break
                            
                            if not account:
                                logger.warning(
                                    f"No integration account found for form_id={form_id}, page_id={page_id}. "
                                    f"Make sure the company has selected a lead form using select_lead_form endpoint."
                                )
                                # لا يمكننا إنشاء IntegrationLog بدون account
                                # لكن يمكننا تسجيل الخطأ في Django logs
                                continue
                            
                            # جلب بيانات الليد من Meta API
                            meta_oauth = MetaOAuth()
                            
                            # الحصول على Page Access Token
                            pages = account.metadata.get('pages', [])
                            page_access_token = None
                            for page in pages:
                                if page.get('id') == page_id:
                                    page_access_token = page.get('access_token')
                                    break
                            
                            if not page_access_token:
                                # محاولة الحصول على Page Access Token من API
                                try:
                                    access_token = account.get_access_token()
                                    if not access_token:
                                        logger.error("No access token available for account")
                                        continue
                                    page_access_token = meta_oauth.get_page_access_token(
                                        page_id,
                                        access_token
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to get page access token: {str(e)}")
                                    continue
                            
                            # جلب بيانات الليد
                            try:
                                lead_data = meta_oauth.get_lead_data(leadgen_id, page_access_token)
                            except Exception as e:
                                logger.error(f"Failed to get lead data: {str(e)}")
                                continue
                            
                            # استخراج البيانات من field_data
                            field_data = lead_data.get('field_data', [])
                            lead_info = {}
                            for field in field_data:
                                lead_info[field.get('name', '').lower()] = field.get('values', [''])[0]
                            
                            # إنشاء Client جديد
                            from crm.models import Client, ClientPhoneNumber, ClientEvent
                            from crm.signals import get_least_busy_employee
                            
                            # استخراج البيانات
                            name = lead_info.get('full_name') or lead_info.get('name') or lead_info.get('first_name', '') + ' ' + lead_info.get('last_name', '')
                            name = name.strip() or 'Unknown'
                            phone = lead_info.get('phone_number') or lead_info.get('phone') or lead_info.get('mobile')
                            email = lead_info.get('email')
                            
                            # البحث عن كامبين مرتبط بهذا الفورم
                            campaign = None
                            form_campaign_mapping = account.metadata.get('form_campaign_mapping', {})
                            if form_id in form_campaign_mapping:
                                from crm.models import Campaign
                                try:
                                    campaign = Campaign.objects.get(
                                        id=form_campaign_mapping[form_id],
                                        company=account.company
                                    )
                                except Campaign.DoesNotExist:
                                    pass
                            
                            # إنشاء Client
                            client = Client.objects.create(
                                name=name,
                                priority='medium',
                                type='fresh',
                                company=account.company,
                                campaign=campaign,
                                source='meta_lead_form',
                                integration_account=account,
                                phone_number=phone,  # للتوافق مع الإصدارات القديمة
                            )
                            
                            # إضافة رقم الهاتف إذا كان موجوداً
                            if phone:
                                ClientPhoneNumber.objects.create(
                                    client=client,
                                    phone_number=phone,
                                    phone_type='mobile',
                                    is_primary=True,
                                )
                            
                            # Auto-assignment إذا كان مفعّل
                            if account.company.auto_assign_enabled:
                                employee = get_least_busy_employee(account.company)
                                if employee:
                                    client.assigned_to = employee
                                    client.assigned_at = timezone.now()
                                    client.save()
                                    
                                    # تسجيل الحدث
                                    ClientEvent.objects.create(
                                        client=client,
                                        event_type='assignment',
                                        old_value='Unassigned',
                                        new_value=employee.get_full_name() or employee.username,
                                        notes=f"Auto-assigned from Meta Lead Form",
                                    )
                            
                            # تسجيل الحدث
                            ClientEvent.objects.create(
                                client=client,
                                event_type='created',
                                new_value='Meta Lead Form',
                                notes=f"Lead created from Meta Lead Form (Form ID: {form_id})",
                            )
                            
                            # تسجيل العملية في IntegrationLog
                            IntegrationLog.objects.create(
                                account=account,
                                action='lead_received',
                                status='success',
                                message=f'Lead received from Meta: {name}',
                                response_data={'leadgen_id': leadgen_id, 'form_id': form_id},
                            )
                            
                            logger.info(f"Successfully created client from Meta lead: {client.id} - {client.name}")
                            
                        except Exception as e:
                            logger.error(f"Error processing Meta webhook: {str(e)}", exc_info=True)
                            # تسجيل الخطأ
                            if 'account' in locals():
                                IntegrationLog.objects.create(
                                    account=account,
                                    action='lead_received',
                                    status='error',
                                    message=f'Failed to process lead from Meta',
                                    error_details=str(e),
                                )
                            continue
            
            return JsonResponse({'status': 'ok'}, status=200)
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON in Meta webhook payload")
            return HttpResponse('Bad Request', status=400)
        except Exception as e:
            logger.error(f"Error processing Meta webhook: {str(e)}", exc_info=True)
            return HttpResponse('Internal Server Error', status=500)


# --------------- Twilio SMS (نقبل Twilio فقط لخدمة SMS) ---------------

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
            return Response({
                'account_sid': '',
                'twilio_number': '',
                'auth_token_masked': None,
                'sender_id': '',
                'is_enabled': False,
            })
        serializer = TwilioSettingsSerializer(twilio_settings)
        return Response(serializer.data)

    if request.method == 'PUT':
        twilio_settings, _ = TwilioSettings.objects.get_or_create(
            company=company,
            defaults={'is_enabled': False},
        )
        serializer = TwilioSettingsSerializer(twilio_settings, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(TwilioSettingsSerializer(twilio_settings).data)


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
        return Response(
            {'error_key': 'sms_error_validation', 'error': 'Invalid request. Check the message and phone number.', 'details': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    data = serializer.validated_data
    lead_id = data['lead_id']
    phone_number = data['phone_number']
    body = data['body']

    company = request.user.company
    try:
        client = Client.objects.get(id=lead_id, company=company)
    except Client.DoesNotExist:
        return Response(
            {'error_key': 'sms_error_lead_not_found', 'error': 'Lead not found or access denied.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        twilio_settings = TwilioSettings.objects.get(company=company, is_enabled=True)
    except TwilioSettings.DoesNotExist:
        return Response(
            {'error_key': 'sms_error_not_configured', 'error': 'SMS is not configured or not enabled. Set it up in Integrations.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account_sid = twilio_settings.account_sid
    auth_token = twilio_settings.get_auth_token()
    twilio_number = twilio_settings.twilio_number
    sender_id = (twilio_settings.sender_id or '').strip()
    # Prefer Sender ID (alphanumeric) when set; otherwise use Twilio number
    from_value = sender_id if sender_id else (twilio_number or '')
    if not account_sid or not auth_token or not from_value:
        return Response(
            {'error_key': 'sms_error_credentials_incomplete', 'error': 'Account SID, Auth Token, and either Sender ID or sender number are required.'},
            status=status.HTTP_400_BAD_REQUEST,
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
        error_key = _twilio_error_to_key(e)
        clean_msg = _strip_ansi(getattr(e, 'msg', None) or str(e))
        if clean_msg and len(clean_msg) > 400:
            clean_msg = clean_msg.split('\n')[0]
        return Response(
            {'error_key': error_key, 'error': clean_msg or 'SMS request was rejected. Please check your settings.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.exception("Twilio send SMS failed")
        clean_msg = _strip_ansi(str(e))
        if len(clean_msg) > 400:
            clean_msg = clean_msg.split('\n')[0]
        return Response(
            {'error_key': 'sms_error_send_failed', 'error': clean_msg or 'Failed to send SMS. Please try again later.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    sms_record = LeadSMSMessage.objects.create(
        client=client,
        phone_number=phone_number,
        body=body,
        direction=LeadSMSMessage.DIRECTION_OUTBOUND,
        twilio_sid=twilio_sid,
        created_by=request.user,
    )
    return Response(
        LeadSMSMessageSerializer(sms_record).data,
        status=status.HTTP_201_CREATED,
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


class MessageTemplateViewSet(viewsets.ModelViewSet):
    """
    قوالب الرسائل لمركز المراسلات (واتساب و SMS).
    CRUD: GET/POST /api/integrations/templates/ , GET/PUT/PATCH/DELETE /api/integrations/templates/:id/
    """
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    serializer_class = MessageTemplateSerializer

    def get_queryset(self):
        return MessageTemplate.objects.filter(company=self.request.user.company).order_by('-updated_at')

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)

