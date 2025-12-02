# دليل التكاملات (Integrations Guide)

هذا الدليل يشرح كيفية تنفيذ التكاملات مع المنصات المختلفة (Meta, TikTok, WhatsApp) في نظام CRM.

## 📋 المحتويات

1. [نظرة عامة](#نظرة-عامة)
2. [إعداد OAuth](#إعداد-oauth)
3. [كيفية عمل OAuth Flow](#كيفية-عمل-oauth-flow)
4. [إضافة تكامل جديد](#إضافة-تكامل-جديد)
5. [استخدام APIs](#استخدام-apis)
6. [أمثلة عملية](#أمثلة-عملية)

---

## نظرة عامة

نظام التكاملات يسمح للشركات بربط حساباتها على منصات التواصل الاجتماعي (Meta, TikTok, WhatsApp) مع نظام CRM. هذا يتيح:

- **مزامنة البيانات**: جلب المنشورات، الإحصائيات، التفاعلات
- **إدارة المحتوى**: نشر المنشورات، إدارة التعليقات
- **تحليل الأداء**: تتبع الإحصائيات والأداء

### البنية المعمارية

```
Frontend (React)
    ↓
Backend API (Django REST Framework)
    ↓
OAuth Utils (oauth_utils.py)
    ↓
External Platform APIs (Meta, TikTok, WhatsApp)
```

---

## إعداد OAuth

### 1. إعداد Meta (Facebook/Instagram)

1. اذهب إلى [Facebook Developers](https://developers.facebook.com/)
2. أنشئ تطبيق جديد
3. أضف منتجات:
   - **Facebook Login**
   - **Instagram Basic Display** (لـ Instagram)
   - **WhatsApp Business API** (لـ WhatsApp)
4. احصل على:
   - `App ID` (Client ID)
   - `App Secret` (Client Secret)
5. أضف Redirect URI:
   ```
   https://your-api-domain.com/api/integrations/accounts/oauth/callback/meta/
   ```
6. أضف المتغيرات في `.env`:
   ```env
   META_CLIENT_ID=your_app_id
   META_CLIENT_SECRET=your_app_secret
   ```

### 2. إعداد TikTok

1. اذهب إلى [TikTok Developers](https://developers.tiktok.com/)
2. أنشئ تطبيق جديد
3. احصل على:
   - `Client Key` (Client ID)
   - `Client Secret`
4. أضف Redirect URI:
   ```
   https://your-api-domain.com/api/integrations/accounts/oauth/callback/tiktok/
   ```
5. أضف المتغيرات في `.env`:
   ```env
   TIKTOK_CLIENT_ID=your_client_key
   TIKTOK_CLIENT_SECRET=your_client_secret
   ```

### 3. إعداد WhatsApp Business API

WhatsApp Business API يستخدم نفس OAuth الخاص بـ Meta، لكن يتطلب:
1. حساب Business Manager في Meta
2. WhatsApp Business Account
3. Phone Number ID

---

## كيفية عمل OAuth Flow

### الخطوات:

1. **المستخدم يضغط "ربط الحساب"**
   ```javascript
   // Frontend
   const response = await connectIntegrationAccountAPI(accountId);
   window.location.href = response.authorization_url;
   ```

2. **المستخدم يوافق على الصلاحيات في المنصة**
   - يتم توجيهه إلى صفحة المنصة
   - يوافق على الصلاحيات المطلوبة

3. **المنصة تعيد التوجيه إلى Callback URL**
   ```
   https://your-api.com/api/integrations/accounts/oauth/callback/meta/?code=xxx&state=yyy
   ```

4. **Backend يستبدل Code بـ Access Token**
   ```python
   # في oauth_utils.py
   token_data = oauth_handler.exchange_code_for_token(code)
   ```

5. **Backend يحفظ Token في Database**
   ```python
   account.access_token = token_data['access_token']
   account.refresh_token = token_data.get('refresh_token')
   account.save()
   ```

6. **إعادة التوجيه إلى Frontend**
   ```
   https://your-frontend.com/integrations?connected=true&account_id=123
   ```

---

## إضافة تكامل جديد

لإضافة منصة جديدة (مثل LinkedIn, Twitter):

### 1. إضافة المنصة إلى Models

```python
# integrations/models.py
class IntegrationPlatform(models.TextChoices):
    # ... المنصات الموجودة
    LINKEDIN = 'linkedin', 'LinkedIn'
    TWITTER = 'twitter', 'Twitter'
```

### 2. إنشاء OAuth Handler

```python
# integrations/oauth_utils.py
class LinkedInOAuth(OAuthBase):
    def __init__(self):
        super().__init__('LINKEDIN')
        self.auth_url = 'https://www.linkedin.com/oauth/v2/authorization'
        self.token_url = 'https://www.linkedin.com/oauth/v2/accessToken'
    
    def get_authorization_url(self, state, scopes=None):
        # تنفيذ منطق LinkedIn OAuth
        pass
    
    def exchange_code_for_token(self, code):
        # تنفيذ استبدال Code بـ Token
        pass
    
    def refresh_token(self, refresh_token):
        # تنفيذ تجديد Token
        pass
    
    def get_user_info(self, access_token):
        # الحصول على معلومات المستخدم
        pass
```

### 3. تحديث get_oauth_handler

```python
# integrations/oauth_utils.py
def get_oauth_handler(platform):
    platform_lower = platform.lower()
    
    if platform_lower == 'linkedin':
        return LinkedInOAuth()
    # ... باقي المنصات
```

### 4. إضافة Settings

```python
# crm_saas_api/settings.py
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = f"{API_BASE_URL}/api/integrations/accounts/oauth/callback/linkedin/"
```

### 5. تحديث Frontend

```typescript
// constants.ts
{ name: 'Integrations', icon: ChevronsUpDownIcon, subItems: ['Meta', 'TikTok', 'WhatsApp', 'LinkedIn'] },

// IntegrationsPage.tsx
const platformConfig = {
    // ...
    'LinkedIn': { name: 'LinkedIn', icon: LinkedInIcon, dataKey: 'linkedin' },
};
```

---

## استخدام APIs

### 1. إنشاء حساب تكامل

```typescript
// Frontend
const account = await createConnectedAccountAPI({
  platform: 'meta',
  name: 'صفحة الفيسبوك الرئيسية',
  account_link: 'https://facebook.com/my-page',
});
```

### 2. ربط الحساب (OAuth)

```typescript
// Frontend
const { authorization_url } = await connectIntegrationAccountAPI(account.id);
// توجيه المستخدم إلى authorization_url
window.location.href = authorization_url;
```

### 3. الحصول على الحسابات المتصلة

```typescript
// Frontend
const accounts = await getConnectedAccountsAPI('meta');
// أو جميع الحسابات
const allAccounts = await getConnectedAccountsAPI();
```

### 4. قطع الاتصال

```typescript
// Frontend
await disconnectIntegrationAccountAPI(accountId);
```

### 5. مزامنة البيانات

```typescript
// Frontend
await syncIntegrationAccountAPI(accountId);
```

---

## أمثلة عملية

### مثال 1: ربط حساب Meta

```typescript
// 1. إنشاء حساب
const account = await createConnectedAccountAPI({
  platform: 'meta',
  name: 'صفحة الشركة',
  account_link: 'https://facebook.com/mycompany',
});

// 2. بدء عملية OAuth
const { authorization_url } = await connectIntegrationAccountAPI(account.id);

// 3. توجيه المستخدم
window.location.href = authorization_url;

// 4. بعد العودة من OAuth، الحساب سيكون متصلاً تلقائياً
```

### مثال 2: جلب المنشورات من Meta

```python
# Backend - يمكن إضافتها كـ action في ViewSet
@action(detail=True, methods=['get'])
def posts(self, request, pk=None):
    account = self.get_object()
    
    if account.platform != 'meta':
        return Response({'error': 'Not supported'}, status=400)
    
    # استخدام Access Token
    url = f"https://graph.facebook.com/v18.0/{account.external_account_id}/posts"
    params = {'access_token': account.access_token}
    
    response = requests.get(url, params=params)
    return Response(response.json())
```

### مثال 3: نشر منشور على Meta

```python
# Backend
@action(detail=True, methods=['post'])
def publish_post(self, request, pk=None):
    account = self.get_object()
    message = request.data.get('message')
    
    url = f"https://graph.facebook.com/v18.0/{account.external_account_id}/feed"
    params = {
        'access_token': account.access_token,
        'message': message,
    }
    
    response = requests.post(url, params=params)
    return Response(response.json())
```

---

## ملاحظات مهمة

### الأمان

1. **لا تعرض Access Token في Frontend**
   - Tokens محفوظة في Backend فقط
   - Frontend لا يحتاج الوصول إليها مباشرة

2. **استخدم HTTPS دائماً**
   - OAuth يتطلب HTTPS في Production

3. **تحقق من State في OAuth Callback**
   - لمنع CSRF attacks

### إدارة Tokens

1. **تجديد Tokens تلقائياً**
   ```python
   if account.is_token_expired():
       token_data = oauth_handler.refresh_token(account.refresh_token)
       account.access_token = token_data['access_token']
       account.save()
   ```

2. **معالجة الأخطاء**
   ```python
   try:
       # استخدام API
   except requests.HTTPError as e:
       if e.response.status_code == 401:
           # Token منتهي، حاول التجديد
           account.refresh_access_token_if_needed()
   ```

### Rate Limiting

- كل منصة لها حدود مختلفة
- استخدم retry logic مع exponential backoff
- احفظ Rate Limit info في metadata

---

## استكشاف الأخطاء

### مشكلة: OAuth Callback لا يعمل

1. تحقق من Redirect URI في إعدادات التطبيق
2. تأكد من تطابق Redirect URI في settings.py
3. تحقق من CORS settings

### مشكلة: Token منتهي

1. تحقق من `token_expires_at`
2. استخدم `refresh_token` لتجديده
3. إذا لم يكن refresh_token متوفر، اطلب من المستخدم إعادة الربط

### مشكلة: الصلاحيات غير كافية

1. تحقق من Scopes المطلوبة
2. أضف Scopes في `get_authorization_url`
3. اطلب من المستخدم إعادة الربط مع الصلاحيات الجديدة

---

## المراجع

- [Meta Graph API Documentation](https://developers.facebook.com/docs/graph-api)
- [TikTok API Documentation](https://developers.tiktok.com/doc/)
- [WhatsApp Business API](https://developers.facebook.com/docs/whatsapp)
- [OAuth 2.0 Specification](https://oauth.net/2/)

---

## الدعم

للمساعدة أو الأسئلة، راجع:
- ملف `oauth_utils.py` للأمثلة
- ملف `views.py` لرؤية كيفية استخدام OAuth handlers
- ملف `models.py` لفهم بنية البيانات

