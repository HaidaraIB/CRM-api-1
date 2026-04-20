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
    WhatsAppEmbeddedSignupCompleteSerializer,
    TwilioSettingsSerializer,
    LeadSMSMessageSerializer,
    SendLeadSMSSerializer,
    LeadWhatsAppMessageSerializer,
    MessageTemplateSerializer,
)
from ..policy import get_effective_integration_policy, get_plan_integration_access
from settings.models import SystemSettings

logger = logging.getLogger(__name__)


def _build_oauth_callback_frontend_url() -> str:
    """
    Build frontend oauth callback URL robustly.
    Handles deployments where app is hosted under /point and FRONTEND_URL may end with /dashboard.
    """
    base = (
        getattr(settings, 'FRONTEND_OAUTH_BASE_URL', None)
        or getattr(settings, 'FRONTEND_URL', None)
        or getattr(settings, 'FRONTEND_APP_URL', None)
        or 'http://localhost:3000'
    ).rstrip('/')
    # If env accidentally points to dashboard page, normalize to app root
    if base.endswith('/dashboard'):
        base = base[:-len('/dashboard')]
    return f"{base}/oauth-callback"


def apply_oauth_token_to_account(account, token_data, user_info):
    """
    Persist OAuth access token, IntegrationAccount metadata, Meta pages, and WhatsAppAccount rows.
    Used by redirect oauth_callback and by WhatsApp Embedded Signup (FB.login) completion.
    """
    oauth_handler = get_oauth_handler(account.platform)
    account.set_access_token(token_data['access_token'])
    if 'refresh_token' in token_data:
        account.set_refresh_token(token_data['refresh_token'])
    expires_in = token_data.get('expires_in', 0)
    if expires_in:
        account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
    account.external_account_id = user_info.get('id') or user_info.get('open_id') or str(account.id)
    account.external_account_name = user_info.get('name') or user_info.get('display_name') or (account.name or 'Meta User')
    account.status = 'connected'
    account.error_message = None
    account.metadata = {
        'user_info': user_info,
        'token_type': token_data.get('token_type', 'Bearer'),
    }
    if account.platform == 'meta' and hasattr(oauth_handler, 'get_pages'):
        try:
            pages = oauth_handler.get_pages(token_data['access_token'])
            account.metadata['pages'] = pages
            for page in pages:
                page['access_token'] = page.get('access_token', '')
        except Exception:
            pass

    if account.platform == 'whatsapp':
        try:
            wa_handler = get_oauth_handler('whatsapp')
            if hasattr(wa_handler, 'get_waba_and_phone_numbers'):
                waba_list = wa_handler.get_waba_and_phone_numbers(token_data['access_token'])
                for item in waba_list:
                    waba_id = item.get('waba_id')
                    business_id = item.get('business_id')
                    for ph in item.get('phone_numbers') or []:
                        phone_number_id = ph.get('id')
                        if not phone_number_id:
                            continue
                        display = (ph.get('display_phone_number') or '').strip()
                        wa_account, _created = WhatsAppAccount.objects.update_or_create(
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
    IntegrationLog.objects.create(
        account=account,
        action='oauth_connect',
        status='success',
        message='Account connected successfully',
    )


class IntegrationAccountViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing integration accounts.

    Endpoints:
    - GET /api/integrations/accounts/ - list accounts
    - GET /api/integrations/accounts/{id}/ - account detail
    - POST /api/integrations/accounts/ - create account
    - PUT /api/integrations/accounts/{id}/ - update account
    - DELETE /api/integrations/accounts/{id}/ - delete account
    - POST /api/integrations/accounts/{id}/connect/ - connect (OAuth)
    - POST /api/integrations/accounts/{id}/disconnect/ - disconnect
    - POST /api/integrations/accounts/{id}/sync/ - sync data
    """
    
    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def _assert_platform_enabled(self, platform: str):
        company = self.request.user.company
        plan_gate = get_plan_integration_access(company, platform)
        if not plan_gate["enabled"]:
            return error_response(
                plan_gate["message"],
                code="plan_integration_not_included",
                status_code=403,
            )
        policies = SystemSettings.get_settings().integration_policies or {}
        effective = get_effective_integration_policy(
            policies,
            company_id=company.id,
            platform=platform,
        )
        if not effective["enabled"]:
            return error_response(effective["message"], code="integration_disabled", status_code=403)
        return None

    def _ensure_metadata_dict(self, account: IntegrationAccount) -> dict:
        if not isinstance(account.metadata, dict):
            account.metadata = {}
        return account.metadata

    def _resolve_meta_page_access_token(self, account: IntegrationAccount, page_id: str, meta_oauth: MetaOAuth):
        metadata = self._ensure_metadata_dict(account)
        pages = metadata.get('pages', []) or []
        page_id_str = str(page_id).strip()
        for page in pages:
            pid = page.get('id')
            if pid is not None and str(pid).strip() == page_id_str and page.get('access_token'):
                return page.get('access_token')

        access_token = account.get_access_token()
        if not access_token:
            return None

        page_access_token = None
        try:
            fresh_pages = meta_oauth.get_pages(access_token)
            for p in fresh_pages:
                if str(p.get('id')).strip() == page_id_str and p.get('access_token'):
                    page_access_token = p.get('access_token')
                    break
            if fresh_pages:
                metadata['pages'] = fresh_pages
        except Exception:
            pass

        if not page_access_token:
            try:
                page_access_token = meta_oauth.get_page_access_token(page_id, access_token)
            except Exception:
                page_access_token = None

        if page_access_token:
            pages = metadata.get('pages', []) or []
            updated = False
            for page in pages:
                pid = page.get('id')
                if pid is not None and str(pid).strip() == page_id_str:
                    page['access_token'] = page_access_token
                    updated = True
                    break
            if not updated:
                pages.append({'id': page_id_str, 'name': page_id_str, 'access_token': page_access_token})
            metadata['pages'] = pages
            account.save(update_fields=['metadata'])

        return page_access_token

    def _auto_subscribe_meta_page(self, account: IntegrationAccount, page_id: str, meta_oauth: MetaOAuth):
        page_access_token = self._resolve_meta_page_access_token(account, page_id, meta_oauth)
        if not page_access_token:
            return {
                'success': False,
                'page_id': str(page_id),
                'message': 'Could not resolve Page access token',
            }
        response_data = meta_oauth.subscribe_page_to_leadgen(page_id, page_access_token)
        success = bool(response_data.get('success') is True)
        if not success and isinstance(response_data, dict):
            # Meta can return "already subscribed" as an error-ish shape on some versions.
            err_msg = str((response_data.get('error') or {}).get('message') or '').lower()
            if 'already subscribed' in err_msg:
                success = True
        return {
            'success': success,
            'page_id': str(page_id),
            'response': response_data,
            'message': 'Subscribed to leadgen successfully' if success else (
                (response_data.get('error') or {}).get('message')
                if isinstance(response_data, dict)
                else 'Failed to subscribe page'
            ),
        }
    
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
    
    def create(self, request, *args, **kwargs):
        platform = request.data.get("platform")
        blocked = self._assert_platform_enabled(platform)
        if blocked is not None:
            return blocked
        return super().create(request, *args, **kwargs)

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
        return success_response(data=platforms)
    
    @action(detail=True, methods=['post'])
    def connect(self, request, pk=None):
        """بدء عملية OAuth لربط الحساب (Meta / WhatsApp). TikTok = Lead Gen فقط."""
        account = self.get_object()
        blocked = self._assert_platform_enabled(account.platform)
        if blocked is not None:
            return blocked
        if account.platform == 'tiktok':
            return error_response(
                'TikTok is Lead Gen only. Use the webhook URL in Integrations → TikTok.',
                code='bad_request',
            )
        try:
            oauth_handler = get_oauth_handler(account.platform)
            state = oauth_handler.generate_state()
            # Store state in DB so callback works across workers (api.loop-crm.app multi-worker)
            OAuthState.objects.create(state=state, account_id=account.id)
            cache.set(f'oauth_state_{state}', account.id, timeout=600)
            request.session[f'oauth_state_{account.id}'] = state
            request.session[f'oauth_account_id_{state}'] = account.id
            auth_url = oauth_handler.get_authorization_url(state)
            data = {'authorization_url': auth_url, 'state': state}
            if account.platform == 'whatsapp':
                cfg = getattr(settings, 'WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID', '') or ''
                app_id = getattr(settings, 'WHATSAPP_CLIENT_ID', '') or ''
                data['embedded_signup'] = {
                    'enabled': bool(cfg and app_id),
                    'app_id': app_id,
                    'config_id': cfg,
                    'graph_api_version': 'v18.0',
                }
            return success_response(data=data)
        except Exception as e:
            return error_response(str(e), code='bad_request')

    @action(detail=True, methods=['post'], url_path='whatsapp/embedded-signup/complete')
    def whatsapp_embedded_signup_complete(self, request, pk=None):
        """
        Complete WhatsApp connection after Meta Embedded Signup (FB.login returns authResponse.code).
        Does not use OAuth state — caller must be authenticated and own this integration account.
        """
        account = self.get_object()
        if account.platform != 'whatsapp':
            return error_response(
                'This action is only for WhatsApp integration accounts.',
                code='bad_request',
            )
        ser = WhatsAppEmbeddedSignupCompleteSerializer(data=request.data)
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        code = ser.validated_data['code']
        oauth_handler = get_oauth_handler('whatsapp')
        embedded_redirect = getattr(
            settings,
            'WHATSAPP_EMBEDDED_SIGNUP_TOKEN_EXCHANGE_REDIRECT_URI',
            '',
        )
        try:
            token_data = oauth_handler.exchange_code_for_token(
                code,
                redirect_uri=embedded_redirect,
            )
        except Exception as e:
            logger.warning("WhatsApp embedded signup token exchange failed: %s", e)
            return error_response(
                'Failed to exchange authorization code for access token.',
                code='bad_request',
                details={'error': str(e)},
            )
        try:
            user_info = oauth_handler.get_user_info(token_data['access_token'])
        except Exception as get_me_err:
            logger.warning("get_user_info (/me) failed after embedded signup: %s", get_me_err)
            user_info = {'id': f'meta_fallback_embedded_{account.id}', 'name': account.name or 'Meta User'}
        try:
            apply_oauth_token_to_account(account, token_data, user_info)
        except Exception as e:
            account.status = 'error'
            account.error_message = str(e)
            account.save()
            IntegrationLog.objects.create(
                account=account,
                action='oauth_connect',
                status='error',
                message='Failed to connect account (embedded signup)',
                error_details=str(e),
            )
            return error_response(str(e), code='bad_request')
        return success_response(
            data={'account_id': account.id, 'connected': True},
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
            return error_response(
                'Missing authorization code',
                code='bad_request',
                details={
                    'detail': 'This endpoint is called by Facebook after user authorization. Please complete the OAuth flow by clicking "Connect" button in the integrations page.',
                    'hint': 'The authorization code should be provided by Facebook in the callback URL.',
                },
            )
        
        serializer = OAuthCallbackSerializer(data=request.query_params or request.data)
        
        if not serializer.is_valid():
            return error_response(
                'Invalid callback parameters',
                code='bad_request',
                details={
                    'errors': serializer.errors,
                    'hint': 'This endpoint expects code and state parameters from Facebook OAuth callback.',
                },
            )
        
        code = serializer.validated_data.get('code')
        state = serializer.validated_data.get('state')
        error = serializer.validated_data.get('error')
        
        if error:
            return error_response(
                str(error),
                code='oauth_error',
                details={'description': serializer.validated_data.get('error_description')},
            )
        
        # التحقق من state: من DB أولاً (يعمل مع عدة workers)، ثم الكاش ثم الجلسة
        account_id = None
        oauth_state_row = OAuthState.objects.filter(state=state).first()
        if oauth_state_row:
            account_id = oauth_state_row.account_id
        if not account_id:
            account_id = cache.get(f'oauth_state_{state}')
        if not account_id:
            account_id = request.session.get(f'oauth_account_id_{state}')
        if not account_id:
            return error_response('Invalid state', code='bad_request')
        
        try:
            # الحصول على account
            account = IntegrationAccount.objects.get(id=account_id)
        except IntegrationAccount.DoesNotExist:
            return error_response(
                'Account not found',
                code='not_found',
                status_code=status.HTTP_404_NOT_FOUND,
            )
        
        try:
            oauth_handler = get_oauth_handler(account.platform)
            token_data = oauth_handler.exchange_code_for_token(code)
            try:
                user_info = oauth_handler.get_user_info(token_data['access_token'])
            except Exception as get_me_err:
                logger.warning("get_user_info (/me) failed: %s. Using fallback.", get_me_err)
                user_info = {'id': f'meta_fallback_{account.id}_{state[:8]}', 'name': account.name or 'Meta User'}
            apply_oauth_token_to_account(account, token_data, user_info)

            OAuthState.objects.filter(state=state).delete()
            cache.delete(f'oauth_state_{state}')
            request.session.pop(f'oauth_state_{account.id}', None)
            request.session.pop(f'oauth_account_id_{state}', None)
            # إعادة التوجيه إلى صفحة النجاح في Frontend (صفحة مخصصة للـ popup تعرض "Connection succeeded" وتطلب إغلاق النافذة)
            callback_url = _build_oauth_callback_frontend_url()
            return redirect(f"{callback_url}?connected=true&account_id={account.id}")
            
        except Exception as e:
            from urllib.parse import quote
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
            
            # Redirect to frontend OAuth callback page with error so popup shows "Connection failed"
            err_msg = quote(str(e)[:200], safe='')
            callback_url = _build_oauth_callback_frontend_url()
            return redirect(f"{callback_url}?connected=false&error={err_msg}")
    
    @action(detail=True, methods=['post'])
    def disconnect(self, request, pk=None):
        """قطع الاتصال مع الحساب. لـ Meta: إلغاء صلاحيات التطبيق من فيسبوك ثم مسح التوكن محلياً."""
        account = self.get_object()

        if account.platform == 'meta' and account.external_account_id:
            access_token = account.get_access_token()
            if access_token:
                try:
                    meta_oauth = MetaOAuth()
                    meta_oauth.revoke_permissions(account.external_account_id, access_token)
                    logger.info("Meta permissions revoked for account %s (user %s)", account.id, account.external_account_id)
                except Exception as e:
                    logger.warning("Meta revoke_permissions failed (token may already be invalid): %s", e)

        account.set_access_token(None)
        account.set_refresh_token(None)
        account.token_expires_at = None
        account.status = 'disconnected'
        account.error_message = None
        account.save()

        IntegrationLog.objects.create(
            account=account,
            action='disconnect',
            status='success',
            message='Account disconnected',
        )

        return success_response(message='Account disconnected successfully')

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None):
        """
        اختبار اتصال الحساب بالمنصة (Meta: التحقق من صلاحية التوكن، وتحديث الصفحات إن لزم).
        POST /api/integrations/accounts/{id}/test-connection/
        """
        account = self.get_object()

        if account.platform != 'meta':
            return error_response(
                'Test connection is only available for Meta accounts',
                code='bad_request',
            )

        if account.status != 'connected':
            return error_response(
                'Account is not connected. Please connect first.',
                code='bad_request',
                details={'valid': False},
            )

        access_token = account.get_access_token()
        if not access_token:
            account.status = 'disconnected'
            account.save()
            return error_response(
                'No access token. Please connect again.',
                code='bad_request',
                details={'valid': False},
            )

        try:
            meta_oauth = MetaOAuth()
            debug_data = meta_oauth.debug_token(access_token)
        except Exception as e:
            logger.warning("test_connection debug_token failed: %s", e)
            account.status = 'expired'
            account.error_message = str(e)[:500]
            account.save()
            return success_response(
                data={'valid': False, 'message': f'Connection check failed: {e}'},
            )

        is_valid = debug_data.get('is_valid') is True
        if not is_valid:
            account.status = 'expired'
            account.error_message = debug_data.get('error', {}).get('message', 'Token is no longer valid')
            account.save()
            return success_response(
                data={
                    'valid': False,
                    'message': 'Token is no longer valid. Please disconnect and connect again.',
                },
            )

        # Token valid: optionally refresh pages list
        try:
            pages = meta_oauth.get_pages(access_token)
            if pages and account.metadata is not None:
                if not isinstance(account.metadata, dict):
                    account.metadata = {}
                account.metadata['pages'] = pages
                account.save(update_fields=['metadata'])
        except Exception as e:
            logger.warning("test_connection get_pages failed: %s", e)

        return success_response(
            data={
                'valid': True,
                'message': 'Connection is valid.',
                'expires_at': debug_data.get('expires_at'),
            },
        )

    def perform_destroy(self, instance):
        """عند حذف الحساب: لـ Meta إلغاء صلاحيات التطبيق من فيسبوك أولاً ثم الحذف."""
        if instance.platform == 'meta' and instance.external_account_id:
            token = instance.get_access_token()
            if token:
                try:
                    meta_oauth = MetaOAuth()
                    meta_oauth.revoke_permissions(instance.external_account_id, token)
                    logger.info("Meta permissions revoked before delete for account %s", instance.id)
                except Exception as e:
                    logger.warning("Meta revoke_permissions before delete failed: %s", e)
        super().perform_destroy(instance)
    
    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """مزامنة البيانات مع المنصة"""
        account = self.get_object()
        blocked = self._assert_platform_enabled(account.platform)
        if blocked is not None:
            return blocked
        
        if account.status != 'connected':
            return error_response('Account is not connected', code='bad_request')
        
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
                    return error_response(
                        'Token expired and no refresh token available',
                        code='bad_request',
                    )
            except Exception as e:
                account.status = 'expired'
                account.error_message = str(e)
                account.save()
                return error_response(
                    f'Failed to refresh token: {str(e)}',
                    code='bad_request',
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
        
        return success_response(message='Sync completed successfully')
    
    @action(detail=False, methods=['get'], url_path='tiktok-leadgen-config')
    def tiktok_leadgen_config(self, request):
        """
        TikTok Lead Gen فقط: إرجاع رابط الويب هوك لهذه الشركة لتسجيله في TikTok Ads Manager.
        GET /api/integrations/accounts/tiktok-leadgen-config/
        """
        company = request.user.company
        base = getattr(settings, 'API_BASE_URL', '').rstrip('/')
        webhook_url = f"{base}/api/integrations/webhooks/tiktok-leadgen/?company_id={company.id}"
        return success_response(
            data={
                'webhook_url': webhook_url,
                'company_id': company.id,
            },
        )
    
    @action(detail=True, methods=['post'], url_path='sync-pages')
    def sync_pages(self, request, pk=None):
        """
        جلب قائمة صفحات فيسبوك للحساب (Meta) وحفظها في metadata.
        يُستخدم عندما لا توجد صفحات محفوظة عند النقر على Select Lead Form.
        POST /api/integrations/accounts/{id}/sync-pages/
        """
        account = self.get_object()
        if account.platform != 'meta':
            return error_response(
                'This endpoint is only available for Meta accounts',
                code='bad_request',
            )
        if account.status != 'connected':
            return error_response('Account is not connected', code='bad_request')
        access_token = account.get_access_token()
        if not access_token:
            return error_response('No access token available', code='bad_request')
        try:
            oauth_handler = get_oauth_handler('meta')
            if not hasattr(oauth_handler, 'get_pages'):
                return error_response(
                    'Pages not supported for this platform',
                    code='bad_request',
                )
            pages = oauth_handler.get_pages(access_token)
            metadata = self._ensure_metadata_dict(account)
            metadata['pages'] = pages
            subscribe_results = []
            for page in pages:
                page['access_token'] = page.get('access_token', '')
                page_id = page.get('id')
                if not page_id:
                    continue
                try:
                    sub = self._auto_subscribe_meta_page(account, str(page_id), oauth_handler)
                    subscribe_results.append(sub)
                    IntegrationLog.objects.create(
                        account=account,
                        action='meta_page_subscribed',
                        status='success' if sub.get('success') else 'error',
                        message=f"Auto-subscribe page {page_id}: {sub.get('message')}",
                        response_data=sub.get('response') if isinstance(sub.get('response'), dict) else {'raw': sub},
                    )
                except Exception as e:
                    subscribe_results.append(
                        {'success': False, 'page_id': str(page_id), 'message': str(e)}
                    )
                    IntegrationLog.objects.create(
                        account=account,
                        action='meta_page_subscribed',
                        status='error',
                        message=f'Auto-subscribe failed for page {page_id}',
                        error_details=str(e),
                    )
            account.save()
            return success_response(data={'pages': pages, 'subscribe_results': subscribe_results})
        except Exception as e:
            logger.exception("sync_pages failed for account %s", account.id)
            return error_response(str(e), code='bad_request')

    @action(detail=True, methods=['get'])
    def lead_forms(self, request, pk=None):
        """
        الحصول على قائمة Lead Forms من صفحة Meta معينة
        
        GET /api/integrations/accounts/{id}/lead_forms/?page_id={page_id}
        """
        account = self.get_object()
        
        if account.platform != 'meta':
            return error_response(
                'This endpoint is only available for Meta accounts',
                code='bad_request',
            )
        
        if account.status != 'connected':
            return error_response('Account is not connected', code='bad_request')
        
        page_id = request.query_params.get('page_id')
        if not page_id:
            return error_response('page_id parameter is required', code='bad_request')
        
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
                access_token = account.get_access_token()
                if not access_token:
                    return error_response(
                        'No access token available',
                        code='bad_request',
                    )
                # جلب الصفحات من /me/accounts (قد يعيد access_token) أفضل من GET /{page_id} الذي قد يعيد 400
                try:
                    fresh_pages = meta_oauth.get_pages(access_token)
                    for p in fresh_pages:
                        if str(p.get('id')) == str(page_id) and p.get('access_token'):
                            page_access_token = p.get('access_token')
                            break
                    if page_access_token and account.metadata.get('pages'):
                        for i, p in enumerate(account.metadata['pages']):
                            if str(p.get('id')) == str(page_id):
                                account.metadata['pages'][i]['access_token'] = page_access_token
                                account.save()
                                break
                except Exception:
                    pass
                if not page_access_token:
                    try:
                        page_access_token = meta_oauth.get_page_access_token(page_id, access_token)
                    except Exception as e:
                        logger.warning("get_page_access_token failed: %s", e)
                    if not page_access_token:
                        return error_response(
                            (
                                'Could not get Page access token. The Meta app needs the "pages_read_engagement" permission. '
                                'Please disconnect this Meta account and connect it again so the new permission is granted.'
                            ),
                            code='bad_request',
                        )
            
            # جلب Lead Forms
            lead_forms = meta_oauth.get_lead_forms(page_id, page_access_token)
            
            return success_response(
                data={
                    'page_id': page_id,
                    'lead_forms': lead_forms,
                },
            )
            
        except Exception as e:
            logger.error(f"Error fetching lead forms: {str(e)}", exc_info=True)
            err_msg = str(e)
            if 'Cannot call API for app' in err_msg or 'on behalf of user' in err_msg:
                err_msg = (
                    'Your Facebook app is in Development mode. Add your Facebook account as a Tester or Developer: '
                    'Meta for Developers → Your App → App roles → Add Test users / Developers. Then try again.'
                )
            elif 'pages_manage_ads' in err_msg:
                err_msg = (
                    'Lead Forms require the "pages_manage_ads" permission. '
                    'Please disconnect this Meta account and connect it again to grant the new permission. '
                    'Details: '
                ) + err_msg
            elif '403' in err_msg or 'Forbidden' in err_msg:
                err_msg = (
                    'Access to Lead Forms was denied (403). The app needs "leads_retrieval" and possibly '
                    '"Leads Access" in Meta for Developers. Disconnect and reconnect the Meta account. Details: '
                ) + err_msg
            return error_response(err_msg, code='bad_request')
    
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
            return error_response(
                'This endpoint is only available for Meta accounts',
                code='bad_request',
            )
        
        page_id = request.data.get('page_id')
        form_id = request.data.get('form_id')
        campaign_id = request.data.get('campaign_id')
        
        if not page_id or not form_id:
            return error_response(
                'page_id and form_id are required',
                code='bad_request',
            )

        self._ensure_metadata_dict(account)
        
        # التحقق من وجود الكامبين إذا تم توفيره
        campaign = None
        if campaign_id:
            from crm.models import Campaign
            try:
                campaign = Campaign.objects.get(id=campaign_id, company=account.company)
            except Campaign.DoesNotExist:
                return error_response(
                    'Campaign not found',
                    code='not_found',
                    status_code=status.HTTP_404_NOT_FOUND,
                )
        
        # تحديث metadata
        if 'form_campaign_mapping' not in account.metadata:
            account.metadata['form_campaign_mapping'] = {}
        
        if campaign_id:
            account.metadata['form_campaign_mapping'][form_id] = campaign_id
        else:
            # إزالة الربط إذا لم يتم توفير campaign_id
            account.metadata['form_campaign_mapping'].pop(form_id, None)
        
        account.metadata['selected_page_id'] = str(page_id)
        account.metadata['selected_form_id'] = str(form_id)
        account.save()

        subscribe_status = {'success': False, 'page_id': str(page_id), 'message': 'Subscription not attempted'}
        try:
            meta_oauth = MetaOAuth()
            subscribe_status = self._auto_subscribe_meta_page(account, str(page_id), meta_oauth)
            IntegrationLog.objects.create(
                account=account,
                action='meta_page_subscribed',
                status='success' if subscribe_status.get('success') else 'error',
                message=f"Select lead form auto-subscribe page {page_id}: {subscribe_status.get('message')}",
                response_data=subscribe_status.get('response') if isinstance(subscribe_status.get('response'), dict) else {'raw': subscribe_status},
            )
        except Exception as e:
            subscribe_status = {'success': False, 'page_id': str(page_id), 'message': str(e)}
            IntegrationLog.objects.create(
                account=account,
                action='meta_page_subscribed',
                status='error',
                message=f'Select lead form auto-subscribe failed for page {page_id}',
                error_details=str(e),
            )
        
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
        
        return success_response(
            data={
                'message': 'Lead form selected successfully',
                'page_id': page_id,
                'form_id': form_id,
                'campaign_id': campaign_id,
                'subscribe_status': subscribe_status,
            },
        )

    @action(detail=True, methods=['get'], url_path='meta-health')
    def meta_health(self, request, pk=None):
        """Meta health diagnostics for selected account/pages."""
        account = self.get_object()
        if account.platform != 'meta':
            return error_response(
                'This endpoint is only available for Meta accounts',
                code='bad_request',
            )

        metadata = self._ensure_metadata_dict(account)
        meta_oauth = MetaOAuth()
        selected_page_id = str(metadata.get('selected_page_id') or '').strip()
        selected_form_id = str(metadata.get('selected_form_id') or '').strip()
        should_subscribe = str(request.query_params.get('subscribe', '')).strip() in {'1', 'true', 'True'}
        requested_page_id = str(request.query_params.get('page_id', '') or '').strip()
        subscribe_target_page_id = requested_page_id or selected_page_id

        token_data = {'valid': False}
        token = account.get_access_token()
        if token:
            try:
                debug_data = meta_oauth.debug_token(token)
                token_data = {
                    'valid': debug_data.get('is_valid') is True,
                    'expires_at': debug_data.get('expires_at'),
                    'scopes': debug_data.get('scopes') or [],
                    'user_id': debug_data.get('user_id'),
                }
            except Exception as e:
                token_data = {'valid': False, 'error': str(e)}

        pages = metadata.get('pages', []) or []
        page_rows = []
        for page in pages:
            page_id = str(page.get('id') or '').strip()
            if not page_id:
                continue
            page_token = page.get('access_token') or self._resolve_meta_page_access_token(account, page_id, meta_oauth)
            row = {
                'id': page_id,
                'name': str(page.get('name') or page_id),
                'has_access_token': bool(page_token),
                'app_installed': False,
                'leadgen_subscribed': False,
                'error': None,
            }
            if not page_token:
                row['error'] = 'Missing Page access token'
            else:
                app_installed, leadgen_subscribed, raw_data, err = meta_oauth.get_subscribed_apps(page_id, page_token)
                row['app_installed'] = app_installed
                row['leadgen_subscribed'] = leadgen_subscribed
                if err:
                    row['error'] = err
                row['subscribed_apps_raw'] = raw_data
                if should_subscribe and subscribe_target_page_id and page_id == subscribe_target_page_id and not leadgen_subscribed:
                    sub = self._auto_subscribe_meta_page(account, page_id, meta_oauth)
                    row['subscribe_attempt'] = sub
                    if sub.get('success'):
                        row['leadgen_subscribed'] = True
                        row['app_installed'] = True
                    IntegrationLog.objects.create(
                        account=account,
                        action='meta_page_subscribed',
                        status='success' if sub.get('success') else 'error',
                        message=f"Meta health subscribe page {page_id}: {sub.get('message')}",
                        response_data=sub.get('response') if isinstance(sub.get('response'), dict) else {'raw': sub},
                    )
            page_rows.append(row)

        page_ids = {str((p or {}).get('id')) for p in pages if (p or {}).get('id') is not None}
        selection = {
            'selected_page_id': selected_page_id or None,
            'selected_form_id': selected_form_id or None,
            'page_in_metadata': selected_page_id in page_ids if selected_page_id else False,
            'campaign_linked': bool((metadata.get('form_campaign_mapping') or {}).get(selected_form_id)),
        }

        now = timezone.now()
        week_ago = now - timedelta(days=7)
        lead_logs = IntegrationLog.objects.filter(account=account, action='lead_received')
        recent_activity = {
            'last_lead_received_at': (
                lead_logs.order_by('-created_at').values_list('created_at', flat=True).first().isoformat()
                if lead_logs.exists()
                else None
            ),
            'leads_last_7d': lead_logs.filter(status='success', created_at__gte=week_ago).count(),
            'errors_last_7d': IntegrationLog.objects.filter(
                account=account,
                status='error',
                created_at__gte=week_ago,
                action__in=['lead_received', 'meta_page_subscribed', 'oauth_connect'],
            ).count(),
        }

        callback_url = f"{getattr(settings, 'API_BASE_URL', '').rstrip('/')}/api/integrations/webhooks/meta/"
        return success_response(
            data={
                'account_id': account.id,
                'status': account.status,
                'token': token_data,
                'webhook': {
                    'callback_url': callback_url,
                    'verify_token_set': bool(getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')),
                    'client_secret_set': bool(getattr(settings, 'META_CLIENT_SECRET', '')),
                },
                'selection': selection,
                'pages': page_rows,
                'recent_activity': recent_activity,
            }
        )


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

