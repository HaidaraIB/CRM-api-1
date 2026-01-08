from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from accounts.permissions import HasActiveSubscription
from .models import IntegrationAccount, IntegrationLog, IntegrationPlatform
from .serializers import (
    IntegrationAccountSerializer,
    IntegrationAccountCreateSerializer,
    IntegrationAccountUpdateSerializer,
    IntegrationAccountDetailSerializer,
    IntegrationLogSerializer,
    OAuthCallbackSerializer,
)
from .oauth_utils import get_oauth_handler, MetaOAuth
from .decorators import rate_limit_webhook
import json
import hmac
import hashlib
import logging

logger = logging.getLogger(__name__)
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
        """الحصول على قائمة المنصات المدعومة"""
        platforms = [
            {
                'value': choice[0],
                'label': choice[1],
            }
            for choice in IntegrationPlatform.choices
        ]
        return Response(platforms)
    
    @action(detail=True, methods=['post'])
    def connect(self, request, pk=None):
        """
        بدء عملية OAuth لربط الحساب
        
        هذا سيرجع رابط OAuth للمستخدم للانتقال إليه
        """
        account = self.get_object()
        
        try:
            oauth_handler = get_oauth_handler(account.platform)
            state = oauth_handler.generate_state()
            
            # حفظ state في session أو database للتحقق لاحقاً
            # يمكن استخدام Redis أو Database
            request.session[f'oauth_state_{account.id}'] = state
            request.session[f'oauth_account_id_{state}'] = account.id
            
            if account.platform == 'tiktok':
                auth_url, code_verifier = oauth_handler.get_authorization_url(state)
                request.session[f'oauth_code_verifier_{account.id}'] = code_verifier
            else:
                auth_url = oauth_handler.get_authorization_url(state)
            
            return Response({
                'authorization_url': auth_url,
                'state': state,
            })
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get', 'post'], url_path='oauth/callback/(?P<platform>[^/]+)')
    def oauth_callback(self, request, platform):
        """
        معالجة OAuth Callback من المنصة
        
        هذا endpoint يتم استدعاؤه من المنصة بعد موافقة المستخدم
        """
        serializer = OAuthCallbackSerializer(data=request.query_params or request.data)
        
        if not serializer.is_valid():
            return Response(
                serializer.errors,
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
            account = IntegrationAccount.objects.get(id=account_id, company=request.user.company)
        except IntegrationAccount.DoesNotExist:
            return Response(
                {'error': 'Account not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            oauth_handler = get_oauth_handler(account.platform)
            
            # استبدال code بـ access token
            if account.platform == 'tiktok':
                code_verifier = request.session.get(f'oauth_code_verifier_{account.id}')
                if not code_verifier:
                    return Response(
                        {'error': 'Code verifier not found'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                token_data = oauth_handler.exchange_code_for_token(code, code_verifier)
            else:
                token_data = oauth_handler.exchange_code_for_token(code)
            
            # الحصول على معلومات المستخدم/الحساب
            user_info = oauth_handler.get_user_info(token_data['access_token'])
            
            # تحديث الحساب (استخدام methods التشفير)
            account.set_access_token(token_data['access_token'])
            if 'refresh_token' in token_data:
                account.set_refresh_token(token_data['refresh_token'])
            
            # حساب تاريخ انتهاء الصلاحية
            expires_in = token_data.get('expires_in', 0)
            if expires_in:
                account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
            
            account.external_account_id = user_info.get('id') or user_info.get('open_id')
            account.external_account_name = user_info.get('name') or user_info.get('display_name')
            account.status = 'connected'
            account.error_message = None
            
            # حفظ معلومات إضافية في metadata
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
            
            # للحصول على WhatsApp Business Account info
            if account.platform == 'whatsapp':
                try:
                    # حفظ Phone Number ID في metadata
                    # يمكن جلبها من Meta Business API
                    # account.metadata['phone_number_id'] = phone_number_id
                    pass
                except Exception as e:
                    pass
            
            account.save()
            
            # تسجيل العملية
            IntegrationLog.objects.create(
                account=account,
                action='oauth_connect',
                status='success',
                message='Account connected successfully',
            )
            
            # تنظيف session
            request.session.pop(f'oauth_state_{account.id}', None)
            request.session.pop(f'oauth_account_id_{state}', None)
            if account.platform == 'tiktok':
                request.session.pop(f'oauth_code_verifier_{account.id}', None)
            
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
        
        # TODO: تنفيذ منطق المزامنة حسب المنصة
        # مثال: جلب المنشورات، الإحصائيات، إلخ
        
        account.last_sync_at = timezone.now()
        account.save()
        
        IntegrationLog.objects.create(
            account=account,
            action='sync',
            status='success',
            message='Data synced successfully',
        )
        
        return Response({'message': 'Sync completed successfully'})
    
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

