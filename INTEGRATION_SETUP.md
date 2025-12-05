# دليل إعداد التكاملات - خطوة بخطوة

## ربط WhatsApp Business API

### الخطوة 1: إعداد Meta Business Manager

1. اذهب إلى [Meta Business Manager](https://business.facebook.com/)
2. أنشئ حساب Business Manager إذا لم يكن لديك واحد
3. أضف صفحة Facebook أو أنشئ صفحة جديدة

### الخطوة 2: إنشاء تطبيق Meta

1. اذهب إلى [Meta for Developers](https://developers.facebook.com/)
2. اضغط على "My Apps" → "Create App"
3. اختر نوع التطبيق: **Business**
4. أدخل اسم التطبيق (مثل: "My CRM Integration")
5. اضغط "Create App"

### الخطوة 3: إضافة WhatsApp Business API

1. في لوحة التحكم للتطبيق، اضغط "Add Product"
2. ابحث عن **WhatsApp** واختره
3. اضغط "Set Up" على WhatsApp

### الخطوة 4: الحصول على Credentials

1. في قسم **WhatsApp** → **API Setup**
2. ستحتاج:
   - **Phone Number ID**: موجود في "From" field
   - **WhatsApp Business Account ID**: موجود في "WhatsApp Business Account"
   - **Temporary Access Token**: موجود في "Temporary access token" (للاختبار فقط)

### الخطوة 5: إعداد OAuth

1. في لوحة التحكم، اذهب إلى **Settings** → **Basic**
2. سجل:
   - **App ID** (Client ID)
   - **App Secret** (Client Secret) - اضغط "Show" لإظهاره
3. في **Settings** → **Basic** → **Add Platform** → اختر **Website**
4. أضف **Site URL**: `http://localhost:8000` (للاختبار)
5. في **Settings** → **Basic** → **App Domains**، أضف: `localhost`

### الخطوة 6: إعداد Redirect URI

1. في **Products** → **WhatsApp** → **Configuration**
2. أضف **Callback URL**: 
   ```
   http://localhost:8000/api/integrations/accounts/oauth/callback/whatsapp/
   ```
   (استبدل `localhost:8000` بـ domain الخاص بك في Production)

### الخطوة 7: إضافة المتغيرات في .env

افتح ملف `.env` في مجلد `CRM-api-1` وأضف:

```env
# Meta/WhatsApp OAuth
META_CLIENT_ID=your_app_id_here
META_CLIENT_SECRET=your_app_secret_here

# Frontend URL (لإعادة التوجيه بعد OAuth)
FRONTEND_URL=http://localhost:3000

# API Base URL
API_BASE_URL=http://localhost:8000
```

### الخطوة 8: تشغيل Migrations

```bash
cd C:\Users\ASUS\Desktop\CRM\CRM-api-1
python manage.py makemigrations integrations
python manage.py migrate
```

### الخطوة 9: استخدام التطبيق

1. افتح التطبيق وانتقل إلى **Integrations** → **WhatsApp**
2. اضغط **Add New Account**
3. أدخل:
   - **Account Name**: (مثل: "WhatsApp Business")
   - **Phone Number**: رقم WhatsApp Business الخاص بك
4. اضغط **Submit**
5. بعد إنشاء الحساب، اضغط **Connect**
6. سيتم توجيهك إلى Meta للموافقة على الصلاحيات
7. بعد الموافقة، سيتم حفظ Access Token تلقائياً

---

## ربط Meta (Facebook/Instagram)

### الخطوة 1: إنشاء تطبيق Meta

1. اذهب إلى [Meta for Developers](https://developers.facebook.com/)
2. اضغط "My Apps" → "Create App"
3. اختر نوع التطبيق: **Business**
4. أدخل اسم التطبيق

### الخطوة 2: إضافة Facebook Login

1. في لوحة التحكم، اضغط "Add Product"
2. اختر **Facebook Login** → **Set Up**
3. اختر **Web** كمنصة

### الخطوة 3: إعداد OAuth Redirect URI

1. في **Facebook Login** → **Settings**
2. أضف **Valid OAuth Redirect URIs**:
   ```
   http://localhost:8000/api/integrations/accounts/oauth/callback/meta/
   ```

### الخطوة 4: طلب الصلاحيات المطلوبة

في **App Review** → **Permissions and Features**، اطلب:
- `pages_show_list`
- `pages_read_engagement`
- `pages_manage_posts`
- `business_management`

### الخطوة 5: إضافة المتغيرات في .env

```env
META_CLIENT_ID=your_app_id_here
META_CLIENT_SECRET=your_app_secret_here
```

---

## ربط TikTok

### الخطوة 1: إنشاء تطبيق TikTok

1. اذهب إلى [TikTok Developers](https://developers.tiktok.com/)
2. اضغط "Create an app"
3. أدخل معلومات التطبيق

### الخطوة 2: الحصول على Credentials

1. في لوحة التحكم، اذهب إلى **Basic Information**
2. سجل:
   - **Client Key** (Client ID)
   - **Client Secret**

### الخطوة 3: إعداد Redirect URI

1. في **Basic Information** → **Redirect URL**
2. أضف:
   ```
   http://localhost:8000/api/integrations/accounts/oauth/callback/tiktok/
   ```

### الخطوة 4: إضافة المتغيرات في .env

```env
TIKTOK_CLIENT_ID=your_client_key_here
TIKTOK_CLIENT_SECRET=your_client_secret_here
```

---

## ملاحظات مهمة

### للاختبار المحلي (Development)

1. استخدم `http://localhost:8000` للـ API
2. استخدم `http://localhost:3000` للـ Frontend
3. تأكد من أن كلا الخادمين يعملان

### للإنتاج (Production)

1. استبدل `localhost` بـ domain الخاص بك
2. استخدم HTTPS (مطلوب لـ OAuth)
3. أضف domain في إعدادات التطبيق في Meta/TikTok

### استكشاف الأخطاء

**مشكلة: "Invalid redirect URI"**
- تأكد من أن Redirect URI في `.env` يطابق تماماً ما في إعدادات التطبيق
- تأكد من استخدام `http://` للاختبار و `https://` للإنتاج

**مشكلة: "App not approved"**
- بعض الصلاحيات تحتاج موافقة من Meta
- استخدم "Development Mode" للاختبار

**مشكلة: "Token expired"**
- Tokens تنتهي صلاحيتها بعد فترة
- استخدم زر "Connect" مرة أخرى لتجديد Token

---

## بعد الربط - الحصول على Leads

بعد ربط الحساب بنجاح، يمكنك:

1. **استخدام Access Token** للوصول إلى APIs
2. **جلب الرسائل/التعليقات** من المنصات
3. **تحويلها إلى Leads** في نظام CRM

ملاحظة: وظيفة جلب Leads تحتاج إلى تنفيذ إضافي حسب احتياجاتك.

