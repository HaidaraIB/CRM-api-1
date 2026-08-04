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
from crm_saas_api.utils import clean_int_query_param

from accounts.permissions import HasActiveSubscription
from ..decorators import rate_limit_webhook
from ..models import (
    IntegrationAccount, IntegrationLog, IntegrationPlatform,
    WhatsAppAccount, OAuthState, TwilioSettings,
    LeadSMSMessage, LeadWhatsAppMessage, MessageTemplate,
)
from ..oauth_utils import get_oauth_handler, MetaOAuth, META_GRAPH_API_VERSION
from ..whatsapp_account_sync import (
    disconnect_whatsapp_accounts_for_integration,
    sync_whatsapp_accounts_from_integration,
    upsert_whatsapp_account_from_embedded_signup,
)
from ..services.whatsapp_coexistence import (
    fetch_phone_registration_fields,
    initiate_smb_app_data_sync,
    is_coexistence_signup_event,
    register_cloud_phone_number,
    subscribe_waba_webhooks,
    verify_coexistence_phone,
)
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
META_REQUEST_CACHE_TTL_SECONDS = 90


def _normalize_meta_page(page: dict | None) -> dict | None:
    if not isinstance(page, dict):
        return None
    page_id = page.get('id')
    if page_id is None:
        return None
    page_id_str = str(page_id).strip()
    if not page_id_str:
        return None
    return {
        'id': page_id_str,
        'name': str(page.get('name') or page_id_str),
        'access_token': str(page.get('access_token') or ''),
    }


def _pick_single_meta_page(pages: list | None, page_id: str | None) -> dict | None:
    target_id = str(page_id or '').strip()
    if not target_id:
        return None
    for page in pages or []:
        normalized = _normalize_meta_page(page)
        if normalized and normalized['id'] == target_id:
            return normalized
    return None


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


def _build_tiktok_company_sig(company_id: int) -> str:
    """
    Build HMAC signature for company-specific TikTok webhook URL.
    This lets us verify company_id on manual TikTok webhook integration.
    """
    raw_secret = (
        getattr(settings, 'TIKTOK_LEADGEN_URL_SIGNING_SECRET', '')
        or getattr(settings, 'TIKTOK_LEADGEN_WEBHOOK_SECRET', '')
        or getattr(settings, 'SECRET_KEY', '')
    )
    secret = str(raw_secret or '').strip()
    if not secret:
        return ''
    return hmac.new(
        secret.encode('utf-8'),
        str(company_id).encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def apply_oauth_token_to_account(account, token_data, user_info, embedded_signup_session=None):
    """
    Persist OAuth access token, IntegrationAccount metadata, Meta pages, and WhatsAppAccount rows.
    Used by redirect oauth_callback and by WhatsApp Embedded Signup (FB.login) completion.

    embedded_signup_session: optional dict with waba_id, phone_number_id, business_id,
    signup_event from Meta WA_EMBEDDED_SIGNUP postMessage (required for display-name-only / 555 numbers).
    Coexistence numbers must NOT be registered via Cloud API /register — they are already registered.
    """
    from ..services.token_lifecycle import upgrade_token_data_to_long_lived

    oauth_handler = get_oauth_handler(account.platform)
    token_data = upgrade_token_data_to_long_lived(account.platform, token_data)
    account.set_access_token(token_data['access_token'])
    if 'refresh_token' in token_data:
        account.set_refresh_token(token_data['refresh_token'])
    expires_in = token_data.get('expires_in', 0)
    if expires_in:
        account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
    account.external_account_id = user_info.get('id') or user_info.get('open_id') or str(account.id)
    display_name = user_info.get('name') or user_info.get('display_name')
    account.external_account_name = display_name or (account.name or 'Meta User')
    if account.platform == 'meta' and display_name and (not account.name or account.name.strip().lower() == 'meta'):
        account.name = display_name
    account.status = 'connected'
    account.error_message = None
    # Preserve prior selection metadata where useful; drop stale token-alert flag.
    prev_meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
    account.metadata = {
        'user_info': user_info,
        'token_type': token_data.get('token_type', 'Bearer'),
    }
    for keep_key in (
        'selected_page_id',
        'selected_form_id',
        'form_campaign_mapping',
        'pixel_id',
        'conversion_leads_enabled',
    ):
        if keep_key in prev_meta and prev_meta.get(keep_key) not in (None, ''):
            account.metadata[keep_key] = prev_meta[keep_key]
    if account.platform == 'meta':
        # Enforce single-page policy: page selection happens later via select_lead_form.
        # Keep existing pages if reconnecting so leadgen keeps working until re-select.
        if prev_meta.get('pages'):
            account.metadata['pages'] = prev_meta.get('pages')
        else:
            account.metadata['pages'] = []

    if account.platform == 'whatsapp':
        token = token_data['access_token']
        session = embedded_signup_session if isinstance(embedded_signup_session, dict) else {}
        waba_id = str(session.get('waba_id') or '').strip()
        phone_number_id = str(session.get('phone_number_id') or '').strip()
        business_id = str(session.get('business_id') or '').strip() or None
        signup_event = str(session.get('signup_event') or '').strip()
        coexistence = is_coexistence_signup_event(signup_event)
        if waba_id and phone_number_id:
            try:
                upsert_whatsapp_account_from_embedded_signup(
                    account,
                    token,
                    waba_id=waba_id,
                    phone_number_id=phone_number_id,
                    business_id=business_id,
                )
            except Exception as e:
                logger.warning("WhatsApp embedded signup session upsert failed: %s", e)
        try:
            sync_whatsapp_accounts_from_integration(account, token)
        except Exception as e:
            logger.warning("WhatsApp WABA/phone fetch failed: %s", e)

        wa_rows = list(
            WhatsAppAccount.objects.filter(
                company=account.company,
                integration_account=account,
                status='connected',
            )
        )

        # Detect coexistence via Graph when postMessage signup_event was missed.
        phone_fields_by_id = {}
        for wa in wa_rows:
            fields = fetch_phone_registration_fields(token, wa.phone_number_id)
            if fields:
                phone_fields_by_id[str(wa.phone_number_id)] = fields
                if fields.get('is_on_biz_app') is True:
                    coexistence = True

        meta = dict(account.metadata or {})
        if signup_event:
            meta['signup_event'] = signup_event
        meta['coexistence'] = bool(coexistence)
        meta['onboarding_mode'] = (
            'whatsapp_business_app' if coexistence else 'cloud_api'
        )
        if phone_fields_by_id:
            # Persist latest Graph health for the first connected number (UI/debug).
            first_fields = next(iter(phone_fields_by_id.values()))
            meta['phone_status'] = first_fields.get('status')
            meta['is_on_biz_app'] = first_fields.get('is_on_biz_app')
            meta['platform_type'] = first_fields.get('platform_type')
            meta['phone_registration_fields'] = phone_fields_by_id
            from integrations.whatsapp_account_sync import _apply_display_name_metadata

            meta = _apply_display_name_metadata(
                meta,
                name_status=first_fields.get('name_status'),
                verified_name=first_fields.get('verified_name'),
            )
        account.metadata = meta

        # Subscribe app to each WABA so webhooks are delivered (hard requirement).
        waba_ids = set()
        if waba_id:
            waba_ids.add(waba_id)
        for wa in wa_rows:
            if wa.waba_id:
                waba_ids.add(str(wa.waba_id))
        subscribe_results = []
        subscribe_ok_any = False
        for wid in waba_ids:
            ok = subscribe_waba_webhooks(token, wid)
            subscribe_results.append({'waba_id': wid, 'ok': ok})
            if ok:
                subscribe_ok_any = True
        meta = dict(account.metadata or {})
        meta['waba_subscribed_apps'] = subscribe_results
        account.metadata = meta
        if waba_ids and not subscribe_ok_any:
            account.status = 'error'
            account.error_message = (
                'WhatsApp connected but webhook subscription failed. '
                'Inbound replies will not arrive until WABA subscribed_apps succeeds. '
                'Run: python manage.py whatsapp_repair_subscriptions'
            )
            IntegrationLog.objects.create(
                account=account,
                action='whatsapp_waba_subscribe',
                status='error',
                message='All WABA subscribed_apps calls failed',
                response_data={'results': subscribe_results},
            )
        elif waba_ids:
            IntegrationLog.objects.create(
                account=account,
                action='whatsapp_waba_subscribe',
                status='success',
                message='WABA subscribed for webhooks',
                response_data={'results': subscribe_results},
            )

        if coexistence:
            # Coexistence: contacts + history sync within Meta's 24h window (do not /register).
            sync_results = []
            for wa in wa_rows:
                verify = verify_coexistence_phone(token, wa.phone_number_id) or phone_fields_by_id.get(
                    str(wa.phone_number_id)
                )
                if verify:
                    meta = dict(account.metadata or {})
                    meta['is_on_biz_app'] = verify.get('is_on_biz_app')
                    meta['platform_type'] = verify.get('platform_type')
                    meta['phone_status'] = verify.get('status') or meta.get('phone_status')
                    account.metadata = meta
                result = initiate_smb_app_data_sync(token, wa.phone_number_id)
                sync_results.append(
                    {
                        'phone_number_id': wa.phone_number_id,
                        'contacts_request_id': (result.get('contacts') or {}).get('request_id')
                        if isinstance(result.get('contacts'), dict)
                        else None,
                        'history_request_id': (result.get('history') or {}).get('request_id')
                        if isinstance(result.get('history'), dict)
                        else None,
                        'errors': result.get('errors') or [],
                    }
                )
            meta = dict(account.metadata or {})
            meta['coexistence_smb_sync'] = sync_results
            account.metadata = meta
            IntegrationLog.objects.create(
                account=account,
                action='whatsapp_coexistence_smb_sync',
                status='success' if not any(r.get('errors') for r in sync_results) else 'error',
                message='Initiated coexistence contacts/history sync',
                response_data={'results': sync_results},
            )
        else:
            # Cloud API: register each phone or sends fail with 133010.
            # Skip if Graph already reports CONNECTED (re-register with a new PIN → 133005).
            register_results = []
            pins = dict((account.metadata or {}).get('cloud_api_two_step_pins') or {})
            for wa in wa_rows:
                prior = phone_fields_by_id.get(str(wa.phone_number_id)) or {}
                if str(prior.get('status') or '').upper() == 'CONNECTED':
                    register_results.append(
                        {
                            'phone_number_id': wa.phone_number_id,
                            'ok': True,
                            'skipped': True,
                            'reason': 'already_connected',
                        }
                    )
                    continue
                existing_pin = pins.get(str(wa.phone_number_id))
                result = register_cloud_phone_number(
                    token,
                    wa.phone_number_id,
                    pin=existing_pin,
                )
                if result.get('pin'):
                    pins[str(wa.phone_number_id)] = result['pin']
                # Refresh status after register (or failed attempt).
                refreshed = fetch_phone_registration_fields(token, wa.phone_number_id)
                if refreshed:
                    phone_fields_by_id[str(wa.phone_number_id)] = refreshed
                already_connected = str((refreshed or {}).get('status') or '').upper() == 'CONNECTED'
                ok = bool(result.get('ok')) or already_connected
                register_results.append(
                    {
                        'phone_number_id': wa.phone_number_id,
                        'ok': ok,
                        'status_code': result.get('status_code'),
                        'error': None if ok else result.get('error'),
                        'skipped': False,
                    }
                )
            meta = dict(account.metadata or {})
            meta['cloud_api_two_step_pins'] = pins
            meta['cloud_api_register'] = register_results
            if phone_fields_by_id:
                first_fields = next(iter(phone_fields_by_id.values()))
                meta['phone_status'] = first_fields.get('status')
                meta['platform_type'] = first_fields.get('platform_type')
                meta['is_on_biz_app'] = first_fields.get('is_on_biz_app')
                meta['phone_registration_fields'] = phone_fields_by_id
                from integrations.whatsapp_account_sync import _apply_display_name_metadata

                meta = _apply_display_name_metadata(
                    meta,
                    name_status=first_fields.get('name_status'),
                    verified_name=first_fields.get('verified_name'),
                )
            account.metadata = meta
            IntegrationLog.objects.create(
                account=account,
                action='whatsapp_cloud_register',
                status='success' if all(r.get('ok') for r in register_results) else 'error',
                message='Cloud API phone registration after Embedded Signup',
                response_data={'results': register_results},
            )
            if register_results and not any(r.get('ok') for r in register_results):
                if account.status == 'connected':
                    account.status = 'error'
                account.error_message = (
                    (account.error_message + ' ' if account.error_message else '')
                    + 'Phone not registered for Cloud API (Graph 133010). '
                    'Reconnect or run whatsapp_repair_subscriptions --register --pin=XXXXXX.'
                ).strip()

    # After Meta reconnect, refresh page tokens so leadgen fetch works immediately.
    if account.platform == 'meta' and account.status == 'connected':
        try:
            pages = oauth_handler.get_pages(token_data['access_token'])
            if pages:
                selected_page_id = str((account.metadata or {}).get('selected_page_id') or '').strip()
                if selected_page_id:
                    pages = [
                        p for p in pages
                        if str((p or {}).get('id') or '').strip() == selected_page_id
                    ] or pages[:1]
                account.metadata = {
                    **(account.metadata or {}),
                    'pages': [
                        {
                            'id': str(p.get('id') or ''),
                            'name': p.get('name') or '',
                            'access_token': p.get('access_token') or '',
                        }
                        for p in pages
                        if p.get('id')
                    ],
                }
        except Exception as e:
            logger.warning("Meta get_pages after connect failed: %s", e)

    account.save()
    IntegrationLog.objects.create(
        account=account,
        action='oauth_connect',
        status='success' if account.status == 'connected' else 'error',
        message=(
            'Account connected successfully'
            if account.status == 'connected'
            else (account.error_message or 'Account connected with errors')
        ),
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

    def _meta_cache_key(self, account_id: int, scope: str, extra: str = '') -> str:
        extra_part = f":{extra}" if extra else ''
        return f"meta_req_cache:{scope}:account:{account_id}{extra_part}"

    def _meta_cache_get(self, account_id: int, scope: str, extra: str = ''):
        return cache.get(self._meta_cache_key(account_id, scope, extra))

    def _meta_cache_set(self, account_id: int, scope: str, data, extra: str = ''):
        cache.set(
            self._meta_cache_key(account_id, scope, extra),
            data,
            timeout=META_REQUEST_CACHE_TTL_SECONDS,
        )

    def _meta_cache_invalidate(self, account_id: int, scopes: list[str] | None = None):
        targets = scopes or ['test_connection', 'sync_pages', 'meta_health']
        for scope in targets:
            cache.delete(self._meta_cache_key(account_id, scope))
            # meta_health currently keys by requested_page_id; clear common variants as well.
            if scope == 'meta_health':
                cache.delete(self._meta_cache_key(account_id, scope, 'default'))

    def _persist_meta_pages_limited(
        self,
        account: IntegrationAccount,
        all_pages_from_graph: list | None,
        selected_page_id: str | None = None,
        save: bool = False,
    ) -> list:
        metadata = self._ensure_metadata_dict(account)
        selected_id = str(
            selected_page_id
            if selected_page_id is not None
            else (metadata.get('selected_page_id') or '')
        ).strip()
        selected_page = _pick_single_meta_page(all_pages_from_graph, selected_id)
        metadata['pages'] = [selected_page] if selected_page else []
        if save:
            account.save(update_fields=['metadata'])
        return metadata['pages']

    def _resolve_meta_page_access_token(self, account: IntegrationAccount, page_id: str, meta_oauth: MetaOAuth):
        metadata = self._ensure_metadata_dict(account)
        pages = metadata.get('pages', []) or []
        page_id_str = str(page_id).strip()
        page_name = page_id_str
        for page in pages:
            pid = page.get('id')
            if pid is not None and str(pid).strip() == page_id_str:
                page_name = str(page.get('name') or page_id_str)
            if pid is not None and str(pid).strip() == page_id_str and page.get('access_token'):
                return page.get('access_token')

        access_token = account.get_access_token()
        if not access_token:
            return None

        page_access_token = None
        try:
            fresh_pages = meta_oauth.get_pages(access_token)
            for p in fresh_pages:
                normalized = _normalize_meta_page(p)
                if normalized and normalized['id'] == page_id_str:
                    page_name = normalized['name']
                    if normalized['access_token']:
                        page_access_token = normalized['access_token']
                    break
        except Exception:
            pass

        if not page_access_token:
            try:
                page_access_token = meta_oauth.get_page_access_token(page_id, access_token)
            except Exception:
                page_access_token = None

        if page_access_token:
            metadata['pages'] = [{
                'id': page_id_str,
                'name': page_name,
                'access_token': page_access_token,
            }]
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
        # TikTok Lead Gen uses an internal row (leadgen_{company_id}); not a user-managed account.
        if self.action == 'list':
            queryset = queryset.exclude(platform='tiktok')

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
        if platform == 'tiktok':
            return error_response(
                'TikTok uses webhook setup only. Configure it under Integrations → TikTok.',
                code='bad_request',
            )
        blocked = self._assert_platform_enabled(platform)
        if blocked is not None:
            return blocked
        return super().create(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        account = self.get_object()
        if account.platform == 'tiktok':
            return error_response(
                'TikTok Lead Gen accounts cannot be deleted. Disable the integration in your plan settings instead.',
                code='bad_request',
            )
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        account = self.get_object()
        if account.platform == 'tiktok':
            return error_response(
                'TikTok Lead Gen accounts are managed automatically via webhook.',
                code='bad_request',
            )
        return super().update(request, *args, **kwargs)

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
                    'graph_api_version': META_GRAPH_API_VERSION,
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
        embedded_session = {
            k: ser.validated_data.get(k)
            for k in ('waba_id', 'phone_number_id', 'business_id', 'signup_event')
            if (ser.validated_data.get(k) or '').strip()
        }
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
            apply_oauth_token_to_account(
                account,
                token_data,
                user_info,
                embedded_signup_session=embedded_session or None,
            )
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
        account.refresh_from_db()
        coexistence = bool((account.metadata or {}).get('coexistence'))
        return success_response(
            data={
                'account_id': account.id,
                'connected': True,
                'coexistence': coexistence,
            },
        )

    @action(detail=True, methods=['post'], url_path='whatsapp/sync-phone-numbers')
    def whatsapp_sync_phone_numbers(self, request, pk=None):
        """
        Re-fetch WABA / phone_number_id from Meta for a connected WhatsApp integration account.
        POST /api/integrations/accounts/:id/whatsapp/sync-phone-numbers/
        """
        account = self.get_object()
        if account.platform != 'whatsapp':
            return error_response(
                'This action is only for WhatsApp integration accounts.',
                code='bad_request',
            )
        if account.status != 'connected':
            return error_response(
                'WhatsApp account is not connected.',
                code='bad_request',
            )
        token = account.get_access_token()
        if not token:
            return error_response(
                'WhatsApp account has no access token.',
                code='whatsapp_no_access_token',
            )

        synced = sync_whatsapp_accounts_from_integration(account, token)
        account.refresh_from_db()
        preferred = str((account.metadata or {}).get('phone_number_id') or '').strip()
        wa = None
        if preferred:
            wa = WhatsAppAccount.objects.filter(
                company=account.company,
                status='connected',
                integration_account=account,
                phone_number_id=preferred,
            ).first()
        if not wa:
            wa = WhatsAppAccount.objects.filter(
                company=account.company,
                status='connected',
                integration_account=account,
            ).first()
        if not wa:
            wa = WhatsAppAccount.objects.filter(
                company=account.company,
                status='connected',
            ).first()

        debug_payload = {}
        try:
            handler = get_oauth_handler('whatsapp')
            if hasattr(handler, 'debug_token'):
                debug_payload = handler.debug_token(token) or {}
        except Exception as e:
            debug_payload = {'error': str(e)}

        if not wa:
            return error_response(
                'Could not load WhatsApp phone numbers from Meta. '
                'Ensure Embedded Signup granted whatsapp_business_messaging on your WABA '
                '(App Review → Permissions → whatsapp_business_messaging should show assets > 0).',
                code='whatsapp_phone_numbers_not_synced',
                details={
                    'synced': synced,
                    'scopes': debug_payload.get('scopes'),
                    'granular_scopes': debug_payload.get('granular_scopes'),
                    'metadata': account.metadata,
                },
            )

        connected_count = WhatsAppAccount.objects.filter(
            company=account.company,
            status='connected',
            integration_account=account,
        ).count()

        return success_response(
            data={
                'synced': synced,
                'phone_number_id': wa.phone_number_id,
                'display_phone_number': wa.display_phone_number,
                'waba_id': wa.waba_id,
                'calling_enabled': wa.calling_enabled,
                'connected_phone_count': connected_count,
            },
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
        if account.platform == 'meta':
            self._meta_cache_invalidate(account.id)
        elif account.platform == 'whatsapp':
            disconnect_whatsapp_accounts_for_integration(account)

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

        cached_result = self._meta_cache_get(account.id, 'test_connection')
        if cached_result is not None:
            return success_response(data=cached_result)

        from ..services.token_lifecycle import mark_account_token_invalid

        try:
            meta_oauth = MetaOAuth()
            debug_data = meta_oauth.debug_token(access_token)
        except Exception as e:
            logger.warning("test_connection debug_token failed: %s", e)
            mark_account_token_invalid(account, error_message=str(e)[:500], notify=True)
            self._meta_cache_invalidate(account.id, scopes=['test_connection', 'meta_health'])
            data = {
                'valid': False,
                'message_key': 'connectionInvalidPleaseReconnect',
                'message': 'Token is no longer valid. Please reconnect Meta.',
            }
            self._meta_cache_set(account.id, 'test_connection', data)
            return success_response(data=data)

        is_valid = debug_data.get('is_valid') is True
        if not is_valid:
            err_msg = debug_data.get('error', {}).get('message', 'Token is no longer valid')
            mark_account_token_invalid(account, error_message=err_msg, notify=True)
            self._meta_cache_invalidate(account.id, scopes=['test_connection', 'meta_health'])
            data = {
                'valid': False,
                'message_key': 'connectionInvalidPleaseReconnect',
                'message': 'Token is no longer valid. Please reconnect Meta.',
            }
            self._meta_cache_set(account.id, 'test_connection', data)
            return success_response(data=data)

        # Token valid: refresh only the selected page metadata entry (if selected)
        try:
            pages = meta_oauth.get_pages(access_token)
            self._persist_meta_pages_limited(account, pages, save=True)
        except Exception as e:
            logger.warning("test_connection get_pages failed: %s", e)

        data = {
            'valid': True,
            'message_key': 'connectionValid',
            'message': 'Connection is valid.',
            'expires_at': debug_data.get('expires_at'),
        }
        self._meta_cache_set(account.id, 'test_connection', data)
        return success_response(data=data)

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
        elif instance.platform == 'whatsapp':
            # Prevent orphaned WhatsAppAccount rows (SET_NULL) from staying connected+tokened.
            disconnect_whatsapp_accounts_for_integration(instance)
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
            from ..services.token_lifecycle import mark_account_token_invalid, refresh_account_token

            try:
                refresh_account_token(account)
            except Exception as e:
                mark_account_token_invalid(account, error_message=str(e)[:500], notify=True)
                return error_response(
                    'Token expired. Please reconnect the account.',
                    code='token_expired_reconnect',
                    status_code=status.HTTP_400_BAD_REQUEST,
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
        # Canonical versioned API URL: /api/v1/...
        if base.endswith('/api/v1'):
            webhook_base = base
        elif base.endswith('/api'):
            webhook_base = f"{base}/v1"
        else:
            webhook_base = f"{base}/api/v1"
        webhook_url = f"{webhook_base}/integrations/webhooks/tiktok-leadgen/?company_id={company.id}"
        sig = _build_tiktok_company_sig(company.id)
        if sig:
            webhook_url = f"{webhook_url}&sig={sig}"
        account = IntegrationAccount.objects.filter(
            company=company,
            platform='tiktok',
            external_account_id=f'leadgen_{company.id}',
        ).first()
        metadata = account.metadata if account and isinstance(account.metadata, dict) else {}
        return success_response(
            data={
                'webhook_url': webhook_url,
                'company_id': company.id,
                'integration_status': (account.status if account else 'disconnected'),
                'last_received_at': metadata.get('last_received_at'),
                'last_sync_at': (account.last_sync_at.isoformat() if account and account.last_sync_at else None),
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
        cached_sync = self._meta_cache_get(account.id, 'sync_pages')
        if cached_sync is not None:
            metadata = self._ensure_metadata_dict(account)
            selected_page_id = str(metadata.get('selected_page_id') or '').strip()
            self._persist_meta_pages_limited(account, cached_sync.get('pages') or [], selected_page_id=selected_page_id)
            account.save(update_fields=['metadata'])
            return success_response(data=cached_sync)
        try:
            oauth_handler = get_oauth_handler('meta')
            if not hasattr(oauth_handler, 'get_pages'):
                return error_response(
                    'Pages not supported for this platform',
                    code='bad_request',
                )
            pages = oauth_handler.get_pages(access_token)
            metadata = self._ensure_metadata_dict(account)
            subscribe_results = []
            selected_page_id = str(metadata.get('selected_page_id') or '').strip()
            self._persist_meta_pages_limited(account, pages, selected_page_id=selected_page_id)
            if selected_page_id:
                try:
                    sub = self._auto_subscribe_meta_page(account, selected_page_id, oauth_handler)
                    subscribe_results.append(sub)
                    IntegrationLog.objects.create(
                        account=account,
                        action='meta_page_subscribed',
                        status='success' if sub.get('success') else 'error',
                        message=f"Auto-subscribe page {selected_page_id}: {sub.get('message')}",
                        response_data=sub.get('response') if isinstance(sub.get('response'), dict) else {'raw': sub},
                    )
                except Exception as e:
                    subscribe_results.append(
                        {'success': False, 'page_id': selected_page_id, 'message': str(e)}
                    )
                    IntegrationLog.objects.create(
                        account=account,
                        action='meta_page_subscribed',
                        status='error',
                        message=f'Auto-subscribe failed for page {selected_page_id}',
                        error_details=str(e),
                    )
            account.save()
            data = {'pages': pages, 'subscribe_results': subscribe_results}
            self._meta_cache_set(account.id, 'sync_pages', data)
            # Pages/subscription state changed; avoid stale health cards.
            self._meta_cache_invalidate(account.id, scopes=['meta_health'])
            return success_response(data=data)
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
        page_id = str(page_id).strip()
        
        try:
            meta_oauth = MetaOAuth()
            
            # الحصول على Page Access Token
            metadata = self._ensure_metadata_dict(account)
            pages = metadata.get('pages', [])
            page_access_token = None
            for page in pages:
                if str(page.get('id') or '').strip() == page_id:
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
                        normalized = _normalize_meta_page(p)
                        if normalized and normalized['id'] == page_id and normalized['access_token']:
                            page_access_token = normalized['access_token']
                            metadata['pages'] = [normalized]
                            account.save(update_fields=['metadata'])
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
        page_id = str(page_id).strip()
        form_id = str(form_id).strip()

        metadata = self._ensure_metadata_dict(account)
        access_token = account.get_access_token()
        if not access_token:
            return error_response('No access token available', code='bad_request')

        try:
            meta_oauth = MetaOAuth()
            available_pages = meta_oauth.get_pages(access_token)
        except Exception as e:
            logger.warning("select_lead_form get_pages failed: %s", e)
            return error_response('Failed to validate selected page with Meta.', code='bad_request')

        selected_page = _pick_single_meta_page(available_pages, page_id)
        if not selected_page:
            return error_response(
                'Selected page is not available for this Meta account.',
                code='bad_request',
            )
        
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
        if 'form_campaign_mapping' not in metadata:
            metadata['form_campaign_mapping'] = {}
        
        if campaign_id:
            metadata['form_campaign_mapping'][form_id] = campaign_id
        else:
            # إزالة الربط إذا لم يتم توفير campaign_id
            metadata['form_campaign_mapping'].pop(form_id, None)
        
        metadata['selected_page_id'] = page_id
        metadata['selected_form_id'] = form_id
        metadata['pages'] = [selected_page]
        account.save()
        self._meta_cache_invalidate(account.id)

        subscribe_status = {'success': False, 'page_id': str(page_id), 'message': 'Subscription not attempted'}
        try:
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
        health_cache_extra = requested_page_id or 'default'

        if not should_subscribe:
            cached_health = self._meta_cache_get(account.id, 'meta_health', extra=health_cache_extra)
            if cached_health is not None:
                return success_response(data=cached_health)

        from ..services.token_lifecycle import mark_account_token_invalid

        token_data = {'valid': False}
        token = account.get_access_token()
        if token:
            try:
                debug_data = meta_oauth.debug_token(token)
                token_valid = debug_data.get('is_valid') is True
                token_data = {
                    'valid': token_valid,
                    'expires_at': debug_data.get('expires_at'),
                    'scopes': debug_data.get('scopes') or [],
                    'user_id': debug_data.get('user_id'),
                    'error': None if token_valid else (
                        (debug_data.get('error') or {}).get('message')
                        or 'Token is no longer valid'
                    ),
                    'error_key': None if token_valid else 'metaTokenSessionInvalidated',
                }
                if not token_valid and account.status == 'connected':
                    mark_account_token_invalid(
                        account,
                        error_message=token_data['error'],
                        notify=True,
                    )
                    account.refresh_from_db(fields=['status', 'error_message'])
            except Exception as e:
                token_data = {
                    'valid': False,
                    'error': str(e),
                    'error_key': 'metaTokenSessionInvalidated',
                }
                if account.status == 'connected':
                    mark_account_token_invalid(account, error_message=str(e)[:500], notify=True)
                    account.refresh_from_db(fields=['status', 'error_message'])

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
        pixel_id = str(metadata.get('pixel_id') or '').strip()
        conversion_leads = {
            'pixel_id': pixel_id or None,
            'pixel_configured': bool(pixel_id),
            'conversion_leads_enabled': metadata.get('conversion_leads_enabled', True) is not False,
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
        data = {
            'account_id': account.id,
            'status': account.status,
            'token': token_data,
            'webhook': {
                'callback_url': callback_url,
                'verify_token_set': bool(getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')),
                'client_secret_set': bool(getattr(settings, 'META_CLIENT_SECRET', '')),
            },
            'selection': selection,
            'conversion_leads': conversion_leads,
            'pages': page_rows,
            'recent_activity': recent_activity,
        }
        if should_subscribe:
            self._meta_cache_invalidate(account.id, scopes=['meta_health'])
        else:
            self._meta_cache_set(account.id, 'meta_health', data, extra=health_cache_extra)
        return success_response(data=data)


class IntegrationLogViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet لعرض سجلات التكامل فقط (قراءة)"""
    
    serializer_class = IntegrationLogSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    
    def get_queryset(self):
        """الحصول على سجلات حسابات الشركة فقط"""
        user = self.request.user
        queryset = IntegrationLog.objects.filter(
            account__company=user.company
        )

        account_id = clean_int_query_param(self.request, 'account')
        if account_id is not None:
            queryset = queryset.filter(account_id=account_id)

        return queryset.order_by('-created_at')


# ==================== WhatsApp Send Message ====================
# إرسال رسالة واتساب: POST إلى Graph API باستخدام phone_number_id و Access Token الخاص بالـ tenant

