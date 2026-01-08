"""
أدوات OAuth للتكامل مع المنصات المختلفة
"""
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
    
    def get_authorization_url(self, state, scopes=None):
        """
        إنشاء رابط التفويض لـ Meta
        
        Scopes المطلوبة:
        - pages_show_list: للحصول على قائمة الصفحات
        - pages_read_engagement: لقراءة التفاعلات
        - pages_manage_posts: لإدارة المنشورات
        - instagram_basic: للوصول إلى Instagram
        """
        if scopes is None:
            scopes = [
                'pages_show_list',
                'pages_read_engagement',
                'pages_manage_posts',
                'instagram_basic',
                'business_management',
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
        """الحصول على معلومات المستخدم"""
        url = f"{self.graph_api_url}/me"
        params = {
            'access_token': access_token,
            'fields': 'id,name,email',
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_pages(self, access_token):
        """الحصول على قائمة الصفحات المرتبطة بالحساب"""
        url = f"{self.graph_api_url}/me/accounts"
        params = {
            'access_token': access_token,
            'fields': 'id,name,access_token,category',
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('data', [])
    
    def get_page_access_token(self, page_id, user_access_token):
        """الحصول على Page Access Token"""
        url = f"{self.graph_api_url}/{page_id}"
        params = {
            'access_token': user_access_token,
            'fields': 'access_token',
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('access_token')
    
    def get_lead_forms(self, page_id, page_access_token):
        """الحصول على قائمة Lead Forms الخاصة بصفحة معينة"""
        url = f"{self.graph_api_url}/{page_id}/leadgen_forms"
        params = {
            'access_token': page_access_token,
            'fields': 'id,name,status,leads_count,created_time',
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('data', [])
    
    def get_lead_data(self, leadgen_id, page_access_token):
        """الحصول على بيانات ليد معين من Meta"""
        url = f"{self.graph_api_url}/{leadgen_id}"
        params = {
            'access_token': page_access_token,
            'fields': 'id,created_time,field_data',
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()


class TikTokOAuth(OAuthBase):
    """OAuth لـ TikTok"""
    
    def __init__(self):
        super().__init__('TIKTOK')
        self.auth_url = 'https://www.tiktok.com/v2/auth/authorize'
        self.token_url = 'https://open.tiktokapis.com/v2/oauth/token'
        self.api_url = 'https://open.tiktokapis.com/v2'
    
    def get_authorization_url(self, state, scopes=None):
        """
        إنشاء رابط التفويض لـ TikTok
        
        Scopes المطلوبة:
        - user.info.basic: معلومات المستخدم الأساسية
        - video.upload: رفع الفيديوهات
        - video.publish: نشر الفيديوهات
        """
        if scopes is None:
            scopes = [
                'user.info.basic',
                'video.upload',
                'video.publish',
            ]
        
        # TikTok يتطلب code_verifier و code_challenge (PKCE)
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip('=')
        
        params = {
            'client_key': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': ','.join(scopes),
            'response_type': 'code',
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }
        
        return f"{self.auth_url}?{urlencode(params)}", code_verifier
    
    def exchange_code_for_token(self, code, code_verifier):
        """استبدال code بـ access token"""
        data = {
            'client_key': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri,
            'code_verifier': code_verifier,
        }
        
        response = requests.post(self.token_url, json=data)
        response.raise_for_status()
        data = response.json()
        
        return {
            'access_token': data.get('access_token'),
            'refresh_token': data.get('refresh_token'),
            'expires_in': data.get('expires_in', 7200),
            'token_type': data.get('token_type', 'Bearer'),
        }
    
    def refresh_token(self, refresh_token):
        """تجديد access token"""
        data = {
            'client_key': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
        
        response = requests.post(self.token_url, json=data)
        response.raise_for_status()
        data = response.json()
        
        return {
            'access_token': data.get('access_token'),
            'refresh_token': data.get('refresh_token'),
            'expires_in': data.get('expires_in', 7200),
        }
    
    def get_user_info(self, access_token):
        """الحصول على معلومات المستخدم"""
        url = f"{self.api_url}/user/info/"
        headers = {
            'Authorization': f'Bearer {access_token}',
        }
        params = {
            'fields': 'open_id,union_id,avatar_url,display_name',
        }
        
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


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
        """الحصول على معلومات WhatsApp Business Account"""
        # WhatsApp Business API endpoint
        url = f"https://graph.facebook.com/v18.0/me/businesses"
        params = {
            'access_token': access_token,
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()


def get_oauth_handler(platform):
    """الحصول على OAuth handler حسب المنصة"""
    platform_lower = platform.lower()
    
    if platform_lower == 'meta':
        return MetaOAuth()
    elif platform_lower == 'tiktok':
        return TikTokOAuth()
    elif platform_lower == 'whatsapp':
        return WhatsAppOAuth()
    else:
        raise ValueError(f"منصة غير مدعومة: {platform}")

