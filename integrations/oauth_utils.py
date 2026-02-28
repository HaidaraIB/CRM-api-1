"""
أدوات OAuth للتكامل مع المنصات المختلفة
"""
import hmac
import requests
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import secrets
import hashlib
import base64
from urllib.parse import urlencode, parse_qs, urlparse


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

