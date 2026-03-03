"""
أدوات OAuth للتكامل مع المنصات المختلفة
"""
import logging
import hmac
import requests
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import secrets
import hashlib
import base64
from urllib.parse import urlencode, parse_qs, urlparse

logger = logging.getLogger(__name__)

# Graph API base for standalone Meta helpers (e.g. subscribed_apps)
META_GRAPH_API_BASE = "https://graph.facebook.com/v18.0"
META_SUBSCRIBED_APPS_TIMEOUT = 15


def subscribe_page_to_leadgen(account, page_id):
    """
    Subscribe a Facebook Page to the app for leadgen webhooks.
    Uses the Page Access Token from account.metadata["pages"].
    Idempotent: safe to call multiple times (already subscribed returns success).

    Returns:
        dict: {"success": bool, "error_code": optional int, "error_message": optional str}
    """
    result = {"success": False}
    page_id = str(page_id).strip()
    if not page_id:
        result["error_message"] = "page_id is required"
        logger.warning("subscribe_page_to_leadgen: empty page_id for account id=%s", getattr(account, "id", None))
        return result

    metadata = getattr(account, "metadata", None) or {}
    if not isinstance(metadata, dict):
        result["error_message"] = "account.metadata is not a dict"
        logger.warning("subscribe_page_to_leadgen: invalid metadata for account id=%s", getattr(account, "id", None))
        return result

    pages = metadata.get("pages") or []
    if not isinstance(pages, list):
        result["error_message"] = "account.metadata['pages'] is not a list"
        logger.warning("subscribe_page_to_leadgen: pages is not a list for account id=%s", getattr(account, "id", None))
        return result

    page_access_token = None
    for p in pages:
        if not isinstance(p, dict):
            continue
        if str(p.get("id")) == page_id:
            page_access_token = (p.get("access_token") or "").strip()
            break

    if not page_access_token:
        result["error_message"] = "Page not found in metadata or missing access_token"
        logger.warning(
            "subscribe_page_to_leadgen: no token for page_id=%s account id=%s",
            page_id,
            getattr(account, "id", None),
        )
        return result

    url = f"{META_GRAPH_API_BASE}/{page_id}/subscribed_apps"
    payload = {"subscribed_fields": "leadgen", "access_token": page_access_token}

    try:
        resp = requests.post(url, data=payload, timeout=META_SUBSCRIBED_APPS_TIMEOUT)
    except requests.exceptions.Timeout:
        result["error_message"] = "Request to Graph API timed out"
        logger.error(
            "subscribe_page_to_leadgen: timeout page_id=%s account id=%s",
            page_id,
            getattr(account, "id", None),
        )
        return result
    except requests.exceptions.RequestException as e:
        result["error_message"] = str(e)
        logger.exception(
            "subscribe_page_to_leadgen: request failed page_id=%s account id=%s",
            page_id,
            getattr(account, "id", None),
        )
        return result

    try:
        data = resp.json()
    except ValueError:
        result["error_message"] = f"Invalid JSON response (status={resp.status_code})"
        logger.error(
            "subscribe_page_to_leadgen: non-JSON response page_id=%s status=%s body=%s",
            page_id,
            resp.status_code,
            (resp.text or "")[:500],
        )
        return result

    if resp.ok:
        success_flag = data.get("success") is True
        result["success"] = success_flag
        if success_flag:
            logger.info(
                "subscribe_page_to_leadgen: subscribed page_id=%s account id=%s",
                page_id,
                getattr(account, "id", None),
            )
        else:
            result["error_message"] = "Graph API returned success=false"
            logger.warning(
                "subscribe_page_to_leadgen: API success=false page_id=%s account id=%s",
                page_id,
                getattr(account, "id", None),
            )
        return result

    err = data.get("error") or {}
    code = err.get("code")
    message = err.get("message", resp.text or "Unknown error")
    result["error_code"] = code
    result["error_message"] = message

    # Log Graph API error codes clearly
    if code == 190:
        logger.warning(
            "subscribe_page_to_leadgen: expired or invalid token (190) page_id=%s account id=%s message=%s",
            page_id,
            getattr(account, "id", None),
            message,
        )
    elif code == 200:
        logger.warning(
            "subscribe_page_to_leadgen: permissions error (200) page_id=%s account id=%s message=%s",
            page_id,
            getattr(account, "id", None),
            message,
        )
    elif code == 102:
        logger.warning(
            "subscribe_page_to_leadgen: session invalid (102) page_id=%s account id=%s message=%s",
            page_id,
            getattr(account, "id", None),
            message,
        )
    else:
        logger.warning(
            "subscribe_page_to_leadgen: Graph API error code=%s page_id=%s account id=%s message=%s",
            code,
            page_id,
            getattr(account, "id", None),
            message,
        )
    return result


class OAuthBase:
    """كلاس أساسي لـ OAuth"""
    
    def __init__(self, platform):
        self.platform = platform
        self.client_id = getattr(settings, f'{platform.upper()}_CLIENT_ID', '')
        self.client_secret = getattr(settings, f'{platform.upper()}_CLIENT_SECRET', '')
        self.redirect_uri = getattr(settings, f'{platform.upper()}_REDIRECT_URI', '')
    
    def generate_state(self):
        """إنشاء state عشوائي للأمان"""
        return secrets.token_urlsafe(32)
    
    def get_authorization_url(self, state):
        """الحصول على رابط التفويض - يجب تنفيذه في كل منصة"""
        raise NotImplementedError
    
    def exchange_code_for_token(self, code):
        """استبدال authorization code بـ access token"""
        raise NotImplementedError
    
    def refresh_token(self, refresh_token):
        """تجديد access token"""
        raise NotImplementedError
    
    def get_user_info(self, access_token):
        """الحصول على معلومات المستخدم"""
        raise NotImplementedError


class MetaOAuth(OAuthBase):
    """OAuth لـ Meta (Facebook/Instagram)"""
    
    def __init__(self):
        super().__init__('META')
        self.auth_url = 'https://www.facebook.com/v18.0/dialog/oauth'
        self.token_url = 'https://graph.facebook.com/v18.0/oauth/access_token'
        self.graph_api_url = 'https://graph.facebook.com/v18.0'

    def _appsecret_proof(self, access_token):
        """مطلوب عند استدعاء Graph API من السيرفر."""
        if not access_token or not self.client_secret:
            return None
        return hmac.new(
            self.client_secret.encode('utf-8'),
            access_token.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()

    def get_authorization_url(self, state, scopes=None):
        """
        إنشاء رابط التفويض لـ Meta
        
        Scopes المستخدمة (يجب أن تكون مضافة في Facebook App وموافق عليها):
        - pages_show_list: قائمة الصفحات
        - pages_read_engagement: قراءة تفاعل الصفحة (مطلوب لـ Page Access Token)
        - pages_manage_ads: إدارة إعلانات الصفحة (مطلوب لـ Lead Forms / leadgen_forms)
        - business_management: إدارة Business
        - leads_retrieval: جلب الليدز من Lead Forms
        """
        if scopes is None:
            scopes = [
                'pages_show_list',
                'pages_read_engagement',
                'pages_manage_ads',
                'business_management',
                'leads_retrieval',
            ]
        
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'state': state,
            'scope': ','.join(scopes),
            'response_type': 'code',
        }
        
        return f"{self.auth_url}?{urlencode(params)}"
    
    def exchange_code_for_token(self, code):
        """استبدال code بـ access token"""
        params = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': code,
        }
        
        response = requests.post(self.token_url, params=params)
        response.raise_for_status()
        data = response.json()
        
        return {
            'access_token': data.get('access_token'),
            'token_type': data.get('token_type', 'Bearer'),
            'expires_in': data.get('expires_in', 5184000),  # 60 يوم افتراضي
        }
    
    def refresh_token(self, access_token):
        """تجديد access token لـ Meta"""
        # Meta تستخدم long-lived tokens
        url = f"{self.graph_api_url}/oauth/access_token"
        params = {
            'grant_type': 'fb_exchange_token',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'fb_exchange_token': access_token,
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        return {
            'access_token': data.get('access_token'),
            'expires_in': data.get('expires_in', 5184000),
        }
    
    def get_user_info(self, access_token):
        """الحصول على معلومات المستخدم من /me (بدون fields لتفادي 400 من بعض التطبيقات)"""
        url = f"{self.graph_api_url}/me"
        params = {'access_token': access_token}
        proof = self._appsecret_proof(access_token)
        if proof:
            params['appsecret_proof'] = proof
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return {'id': data.get('id'), 'name': data.get('name') or ''}
    
    def get_pages(self, access_token):
        """الحصول على قائمة الصفحات. نجرّب /me/accounts مع access_token أولاً ثم بدون."""
        proof = self._appsecret_proof(access_token) or ''
        url = f"{self.graph_api_url}/me/accounts"
        # محاولة 1: مع access_token (يتطلب pages_read_engagement) لاستخدامه في Lead Forms
        for fields in ('id,name,access_token', 'id,name'):
            params = {'access_token': access_token, 'fields': fields}
            if proof:
                params['appsecret_proof'] = proof
            response = requests.get(url, params=params)
            if response.ok:
                data = response.json().get('data', [])
                for page in data:
                    page.setdefault('access_token', '')
                return data
            if fields == 'id,name':
                try:
                    err = response.json().get('error', {})
                    msg = err.get('message', response.text)
                    code = err.get('code', '')
                    raise requests.exceptions.HTTPError(
                        f"Facebook API error ({code}): {msg}",
                        response=response
                    )
                except (ValueError, KeyError):
                    response.raise_for_status()
        # محاولة 2: /me?fields=accounts{id,name} كبديل
        try:
            params1 = {'access_token': access_token, 'fields': 'accounts{id,name}'}
            if proof:
                params1['appsecret_proof'] = proof
            r1 = requests.get(f"{self.graph_api_url}/me", params=params1)
            r1.raise_for_status()
            data = r1.json().get('accounts', {}).get('data', [])
            if data:
                for page in data:
                    page.setdefault('access_token', '')
                return data
        except requests.exceptions.HTTPError:
            pass
        return []
    
    def get_page_access_token(self, page_id, user_access_token):
        """الحصول على Page Access Token. نجرّب بدون appsecret_proof أولاً (بعض الطلبات تقبله فقط بدون proof)."""
        url = f"{self.graph_api_url}/{page_id}"
        params = {'access_token': user_access_token, 'fields': 'access_token'}
        for use_proof in (False, True):
            if use_proof:
                proof = self._appsecret_proof(user_access_token)
                if proof:
                    params['appsecret_proof'] = proof
            elif 'appsecret_proof' in params:
                params.pop('appsecret_proof', None)
            response = requests.get(url, params=params)
            if response.ok:
                return response.json().get('access_token')
            if use_proof:
                try:
                    err = response.json().get('error', {})
                    raise requests.exceptions.HTTPError(
                        f"Facebook API error: {err.get('message', response.text)}",
                        response=response
                    )
                except (ValueError, KeyError):
                    response.raise_for_status()
        return None
    
    def get_lead_forms(self, page_id, page_access_token):
        """الحصول على قائمة Lead Forms. نجرّب بدون appsecret_proof أولاً (قد يعيد 403 معه)."""
        url = f"{self.graph_api_url}/{page_id}/leadgen_forms"
        params = {
            'access_token': page_access_token,
            'fields': 'id,name,status,leads_count,created_time',
        }
        last_error = None
        for use_proof in (False, True):
            if use_proof:
                proof = self._appsecret_proof(page_access_token)
                if proof:
                    params['appsecret_proof'] = proof
            elif 'appsecret_proof' in params:
                params.pop('appsecret_proof', None)
            response = requests.get(url, params=params)
            if response.ok:
                return response.json().get('data', [])
            try:
                err = response.json().get('error', {})
                last_error = err.get('message', response.text)
                if err.get('code'):
                    last_error = f"({err['code']}) {last_error}"
            except (ValueError, KeyError):
                last_error = response.text or str(response.reason)
        raise requests.exceptions.HTTPError(
            f"Facebook API: {last_error}",
            response=response
        )
    
    def get_lead_data(self, leadgen_id, page_access_token):
        """الحصول على بيانات ليد معين من Meta"""
        url = f"{self.graph_api_url}/{leadgen_id}"
        params = {
            'access_token': page_access_token,
            'fields': 'id,created_time,field_data',
        }
        proof = self._appsecret_proof(page_access_token)
        if proof:
            params['appsecret_proof'] = proof
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_app_access_token(self):
        """App access token for server-side API calls (e.g. debug_token)."""
        url = f"{self.graph_api_url}/oauth/access_token"
        params = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials',
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get('access_token', '')

    def debug_token(self, user_access_token):
        """
        Check if the user access token is still valid.
        Returns dict with is_valid, expires_at, user_id, scopes, etc.
        """
        app_token = self.get_app_access_token()
        if not app_token:
            return {'is_valid': False, 'error': 'Could not get app access token'}
        url = f"{self.graph_api_url}/debug_token"
        params = {
            'input_token': user_access_token,
            'access_token': app_token,
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json().get('data', {})
        return data

    def revoke_permissions(self, user_id, user_access_token):
        """
        Revoke app permissions for the user (disconnect app from user's Facebook).
        DELETE /{user-id}/permissions
        """
        url = f"{self.graph_api_url}/{user_id}/permissions"
        params = {'access_token': user_access_token}
        proof = self._appsecret_proof(user_access_token)
        if proof:
            params['appsecret_proof'] = proof
        response = requests.delete(url, params=params)
        if response.status_code == 200:
            return True
        try:
            err = response.json().get('error', {})
            raise requests.exceptions.HTTPError(
                f"Facebook API: {err.get('message', response.text)}",
                response=response,
            )
        except (ValueError, KeyError):
            response.raise_for_status()
        return False


# TikTok: لا نستخدم OAuth (Login Kit) هنا. TikTok في هذا المشروع = Lead Gen فقط (ويب هوك استقبال الليدز).
# انظر integrations/views.py → tiktok_leadgen_webhook و READMEs/TIKTOK_LEADGEN_TIKTOK_FOR_BUSINESS_GUIDE.md


class WhatsAppOAuth(OAuthBase):
    """
    OAuth لـ WhatsApp Business API
    
    ملاحظة: WhatsApp Business API يتطلب:
    1. حساب Business Manager في Meta
    2. WhatsApp Business Account
    3. Phone Number ID
    """
    
    def __init__(self):
        super().__init__('WHATSAPP')
        # WhatsApp يستخدم نفس OAuth لـ Meta
        self.meta_oauth = MetaOAuth()
    
    def get_authorization_url(self, state):
        """استخدام Meta OAuth لـ WhatsApp"""
        return self.meta_oauth.get_authorization_url(
            state,
            scopes=[
                'whatsapp_business_management',
                'whatsapp_business_messaging',
            ]
        )
    
    def exchange_code_for_token(self, code):
        """استخدام Meta OAuth"""
        return self.meta_oauth.exchange_code_for_token(code)
    
    def refresh_token(self, refresh_token):
        """استخدام Meta OAuth"""
        return self.meta_oauth.refresh_token(refresh_token)
    
    def get_user_info(self, access_token):
        """الحصول على معلومات المستخدم/Business"""
        url = "https://graph.facebook.com/v18.0/me"
        params = {
            'access_token': access_token,
            'fields': 'id,name',
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_waba_and_phone_numbers(self, access_token):
        """
        جلب WABA ID و Phone Number IDs بعد OAuth.
        يُستدعى بعد exchange_code_for_token.
        Returns: list of dicts [{"waba_id", "business_id", "phone_numbers": [{"id", "display_phone_number"}, ...]}, ...]
        """
        graph = "https://graph.facebook.com/v18.0"
        out = []
        # محاولة 1: من me/accounts (صفحات قد ترتبط بـ WABA)
        try:
            # قائمة الـ Businesses التي يملكها المستخدم
            resp = requests.get(
                f"{graph}/me/businesses",
                params={
                    'access_token': access_token,
                    'fields': 'id,name,owned_whatsapp_business_accounts',
                },
            )
            resp.raise_for_status()
            data = resp.json()
            businesses = data.get('data') or []
        except Exception:
            businesses = []
        for biz in businesses:
            business_id = biz.get('id')
            wabas = (biz.get('owned_whatsapp_business_accounts') or {}).get('data') or []
            for waba in wabas:
                waba_id = waba.get('id')
                if not waba_id:
                    continue
                try:
                    ph_resp = requests.get(
                        f"{graph}/{waba_id}/phone_numbers",
                        params={'access_token': access_token},
                    )
                    ph_resp.raise_for_status()
                    phones = (ph_resp.json().get('data') or [])
                except Exception:
                    phones = []
                out.append({
                    'waba_id': waba_id,
                    'business_id': business_id,
                    'phone_numbers': [
                        {'id': p.get('id'), 'display_phone_number': p.get('display_phone_number') or ''}
                        for p in phones if p.get('id')
                    ],
                })
        # إذا لم نجد من businesses، نجرب me?fields=whatsapp_business_accounts
        if not out:
            try:
                resp = requests.get(
                    f"{graph}/me",
                    params={
                        'access_token': access_token,
                        'fields': 'id,name,whatsapp_business_accounts',
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                waba_list = (data.get('whatsapp_business_accounts') or {}).get('data') or []
                for waba in waba_list:
                    waba_id = waba.get('id')
                    if not waba_id:
                        continue
                    try:
                        ph_resp = requests.get(
                            f"{graph}/{waba_id}/phone_numbers",
                            params={'access_token': access_token},
                        )
                        ph_resp.raise_for_status()
                        phones = (ph_resp.json().get('data') or [])
                    except Exception:
                        phones = []
                    out.append({
                        'waba_id': waba_id,
                        'business_id': None,
                        'phone_numbers': [
                            {'id': p.get('id'), 'display_phone_number': p.get('display_phone_number') or ''}
                            for p in phones if p.get('id')
                        ],
                    })
            except Exception:
                pass
        return out


def get_oauth_handler(platform):
    """الحصول على OAuth handler حسب المنصة (TikTok = Lead Gen فقط، لا OAuth)"""
    platform_lower = platform.lower()
    if platform_lower == 'tiktok':
        raise ValueError("TikTok integration is Lead Gen only. Use webhook URL in TikTok Ads Manager.")
    if platform_lower == 'meta':
        return MetaOAuth()
    if platform_lower == 'whatsapp':
        return WhatsAppOAuth()
    raise ValueError(f"منصة غير مدعومة: {platform}")

