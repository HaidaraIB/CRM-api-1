# متطلبات WhatsApp Webhook - دليل شامل

## 📋 نظرة عامة

WhatsApp Webhook يحتاج إلى عدة متطلبات لإعداده بشكل صحيح. هذا الدليل يوضح كل ما تحتاجه.

---

## ✅ المتطلبات الأساسية

### 1. **Meta App (Facebook App)** 🔴

WhatsApp Business API يعمل من خلال Meta App (Facebook App). يجب أن يكون لديك:

- ✅ **Meta App** منشأ في [Facebook Developers](https://developers.facebook.com/)
- ✅ **WhatsApp Business Account** مرتبط بالـ App
- ✅ **Phone Number ID** من WhatsApp Business API
- ✅ **Access Token** للوصول إلى WhatsApp API

#### خطوات الحصول على Meta App:

1. اذهب إلى: https://developers.facebook.com/
2. أنشئ App جديد → اختر **"Business"**
3. أضف Product: **"WhatsApp"**
4. اتبع خطوات إعداد WhatsApp Business API

---

### 2. **متغيرات البيئة (Environment Variables)** 🔴

يجب إضافة هذه المتغيرات في ملف `.env`:

```env
# ==================== Meta/WhatsApp Integration ====================
META_CLIENT_ID=your_meta_app_id
META_CLIENT_SECRET=your_meta_app_secret
META_WEBHOOK_VERIFY_TOKEN=your_secure_verify_token

# ==================== API Base URL ====================
# مهم جداً لبناء Webhook URL
API_BASE_URL=https://yourdomain.com
# أو للاختبار المحلي:
# API_BASE_URL=http://localhost:8000

# ==================== Encryption ====================
# لتشفير Access Tokens
INTEGRATION_ENCRYPTION_KEY=your_32_character_base64_key
```

#### كيفية إنشاء `META_WEBHOOK_VERIFY_TOKEN`:

```bash
# الطريقة 1: Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# الطريقة 2: OpenSSL
openssl rand -hex 32
```

**مثال**: `aB3xY9mN2pQ7rT5vW8zC1dF4gH6jK0lM`

#### كيفية إنشاء `INTEGRATION_ENCRYPTION_KEY`:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

### 3. **Webhook URL** 🔴

يجب أن يكون لديك URL عام (publicly accessible) لاستقبال Webhooks:

#### للاختبار المحلي:
استخدم **ngrok** أو **localtunnel**:

```bash
# تثبيت ngrok
# https://ngrok.com/download

# تشغيل ngrok
ngrok http 8000
```

ستحصل على URL مثل: `https://abc123.ngrok.io`

#### للإنتاج:
يجب أن يكون لديك domain مع SSL certificate:

```
https://yourdomain.com/api/integrations/webhooks/whatsapp/
```

**⚠️ مهم:**
- يجب أن يكون HTTPS (وليس HTTP) في الإنتاج
- يجب أن يكون URL عام (يمكن الوصول إليه من الإنترنت)
- يجب أن ينتهي بـ `/whatsapp/`

---

### 4. **إعداد Webhook في Meta App** 🔴

#### الخطوات:

1. اذهب إلى [Meta App Dashboard](https://developers.facebook.com/)
2. اختر App الخاص بك
3. اذهب إلى **Products** → **WhatsApp** → **Configuration**
4. أو اذهب إلى **Settings** → **Webhooks**
5. اضغط **"Add Callback URL"** أو **"Create Webhook"**
6. أدخل:
   - **Callback URL**: 
     ```
     https://yourdomain.com/api/integrations/webhooks/whatsapp/
     ```
     - للاختبار المحلي: `https://abc123.ngrok.io/api/integrations/webhooks/whatsapp/`
   
   - **Verify Token**: 
     ```
     نفس القيمة في META_WEBHOOK_VERIFY_TOKEN
     ```
     - مثال: `aB3xY9mN2pQ7rT5vW8zC1dF4gH6jK0lM`

7. اضغط **"Verify and Save"**

#### إضافة Subscription Fields:

بعد إضافة Webhook، اضغط على **"Edit"** بجانب Webhook:

1. في **Subscription Fields**، أضف:
   - `messages` ⭐ (مهم جداً! لاستقبال الرسائل)
   - `message_status` (اختياري - لتحديثات حالة الرسالة)

---

### 5. **التحقق من Webhook (Verification)** ✅

عند إضافة Webhook، Meta سيرسل طلب GET للتحقق:

```
GET /api/integrations/webhooks/whatsapp/?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=RANDOM_STRING
```

**الكود موجود في:** `integrations/whatsapp_webhook.py`

```python
if request.method == 'GET':
    mode = request.GET.get('hub.mode')
    token = request.GET.get('hub.verify_token')
    challenge = request.GET.get('hub.challenge')
    
    verify_token = getattr(settings, 'META_WEBHOOK_VERIFY_TOKEN', '')
    
    if mode == 'subscribe' and token == verify_token:
        return HttpResponse(challenge, content_type='text/plain')
    else:
        return HttpResponse('Forbidden', status=403)
```

**✅ يجب أن يعمل تلقائياً إذا كان `META_WEBHOOK_VERIFY_TOKEN` صحيح.**

---

### 6. **التحقق من التوقيع (Signature Verification)** 🔐

WhatsApp يرسل توقيع مع كل طلب POST للتحقق من أن الطلب أتى من Meta:

**Header:** `X-Hub-Signature-256`

**الكود موجود في:** `integrations/whatsapp_webhook.py`

```python
def verify_whatsapp_webhook_signature(request):
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature:
        return False
    
    if not signature.startswith('sha256='):
        return False
    
    received_signature = signature[7:]
    
    # WhatsApp يستخدم نفس App Secret مثل Meta
    app_secret = getattr(settings, 'META_CLIENT_SECRET', '')
    if not app_secret:
        return False
    
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        request.body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(received_signature, expected_signature)
```

**✅ يجب أن يعمل تلقائياً إذا كان `META_CLIENT_SECRET` صحيح.**

---

### 7. **هيكل البيانات المتوقع** 📦

#### عند استقبال رسالة WhatsApp:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "1234567890",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "messages": [
              {
                "from": "1234567890",
                "id": "wamid.xxx",
                "timestamp": "1234567890",
                "type": "text",
                "text": {
                  "body": "Hello, I need information"
                }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

#### معالجة الرسالة:

الكود موجود في: `integrations/whatsapp_webhook.py` → `process_whatsapp_message()`

```python
def process_whatsapp_message(message, phone_number_id):
    from_number = message.get('from')  # رقم المرسل
    message_id = message.get('id')
    message_type = message.get('type')  # text, image, etc.
    
    if message_type == 'text':
        text_body = message.get('text', {}).get('body', '')
    
    # البحث عن IntegrationAccount المرتبط
    account = IntegrationAccount.objects.filter(
        platform='whatsapp',
        status='connected',
        metadata__contains={'phone_number_id': phone_number_id}
    ).first()
    
    # البحث عن Client أو إنشاء جديد
    # ... (الكود موجود في الملف)
```

---

### 8. **IntegrationAccount في قاعدة البيانات** 💾

يجب أن يكون لديك `IntegrationAccount` مرتبط بـ WhatsApp:

```python
IntegrationAccount.objects.create(
    company=company,
    platform='whatsapp',
    status='connected',
    metadata={
        'phone_number_id': 'PHONE_NUMBER_ID',
        'access_token': 'ENCRYPTED_TOKEN',
        'phone_number': '1234567890',
    }
)
```

**كيفية الحصول على Phone Number ID:**
- من Meta App Dashboard → WhatsApp → Configuration
- أو من WhatsApp Business API

---

### 9. **Rate Limiting** ⚡

الكود يحتوي على Rate Limiting لمنع الإساءة:

```python
@rate_limit_webhook(max_requests=100, window=60)
def whatsapp_webhook(request):
    # ...
```

**الحد:** 100 طلب في 60 ثانية لكل IP

---

### 10. **CSRF Exemption** 🔓

Webhook endpoint معفى من CSRF لأن Meta يرسل الطلبات من خارج Django:

```python
@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    # ...
```

---

## 📝 قائمة التحقق (Checklist)

### إعداد Meta App:
- [ ] إنشاء Meta App (Business type)
- [ ] إضافة Product: WhatsApp
- [ ] الحصول على `META_CLIENT_ID`
- [ ] الحصول على `META_CLIENT_SECRET`
- [ ] إعداد WhatsApp Business Account
- [ ] الحصول على Phone Number ID

### إعداد Backend:
- [ ] إضافة `META_CLIENT_ID` في `.env`
- [ ] إضافة `META_CLIENT_SECRET` في `.env`
- [ ] إنشاء `META_WEBHOOK_VERIFY_TOKEN` وإضافته في `.env`
- [ ] إنشاء `INTEGRATION_ENCRYPTION_KEY` وإضافته في `.env`
- [ ] إضافة `API_BASE_URL` في `.env`
- [ ] تشغيل Migrations
- [ ] اختبار Webhook Verification (GET request)

### إعداد Webhook:
- [ ] الحصول على Public URL (ngrok للإنتاج أو domain للإنتاج)
- [ ] إضافة Webhook URL في Meta App
- [ ] إضافة Verify Token في Meta App
- [ ] إضافة Subscription Fields: `messages`
- [ ] التحقق من نجاح Verification
- [ ] اختبار استقبال رسالة WhatsApp

### اختبار:
- [ ] إرسال رسالة WhatsApp إلى رقم Business
- [ ] التحقق من استقبال Webhook
- [ ] التحقق من إنشاء/تحديث Client في قاعدة البيانات
- [ ] التحقق من إرسال إشعار FCM (إذا كان مُعد)

---

## 🧪 الاختبار

### 1. اختبار Webhook Verification:

```bash
# محاكاة طلب GET من Meta
curl "http://localhost:8000/api/integrations/webhooks/whatsapp/?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123"
```

**النتيجة المتوقعة:** `test123` (يجب أن يعيد challenge كما هو)

### 2. اختبار استقبال رسالة:

```bash
# محاكاة طلب POST من Meta
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=..." \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "changes": [{
        "value": {
          "messages": [{
            "from": "1234567890",
            "id": "wamid.test",
            "timestamp": "1234567890",
            "type": "text",
            "text": {"body": "Test message"}
          }],
          "metadata": {
            "phone_number_id": "PHONE_NUMBER_ID"
          }
        }
      }]
    }]
  }' \
  http://localhost:8000/api/integrations/webhooks/whatsapp/
```

### 3. اختبار من WhatsApp فعلي:

1. أرسل رسالة WhatsApp إلى رقم Business الخاص بك
2. تحقق من Logs:
   ```bash
   python manage.py runserver
   # ستظهر جميع الطلبات في Console
   ```
3. تحقق من قاعدة البيانات:
   ```python
   from crm.models import Client
   from integrations.models import IntegrationLog
   
   # تحقق من إنشاء Client
   Client.objects.filter(source='whatsapp').latest('created_at')
   
   # تحقق من IntegrationLog
   IntegrationLog.objects.filter(action='whatsapp_message_received').latest('created_at')
   ```

---

## 🔍 استكشاف الأخطاء

### المشكلة: Webhook Verification Failed

**الأعراض:**
- Meta يعرض خطأ عند إضافة Webhook
- GET request يرجع 403

**الحلول:**
1. ✅ تحقق من تطابق `META_WEBHOOK_VERIFY_TOKEN` في `.env` مع Meta App
2. ✅ تحقق من أن URL ينتهي بـ `/whatsapp/`
3. ✅ تحقق من أن Server يعمل
4. ✅ تحقق من Logs:
   ```bash
   python manage.py runserver
   # ستظهر رسائل التحقق
   ```

### المشكلة: Signature Verification Failed

**الأعراض:**
- POST requests ترجع 401
- Logs تظهر: "WhatsApp webhook signature verification failed"

**الحلول:**
1. ✅ تحقق من وجود `META_CLIENT_SECRET` في `.env`
2. ✅ تحقق من تطابق `META_CLIENT_SECRET` مع Meta App
3. ✅ تحقق من أن Header `X-Hub-Signature-256` موجود

### المشكلة: لا يتم استقبال الرسائل

**الأعراض:**
- Webhook Verification نجح
- لكن لا تصل رسائل WhatsApp

**الحلول:**
1. ✅ تحقق من Subscription Fields في Meta App:
   - يجب أن يكون `messages` مضاف
2. ✅ تحقق من Phone Number ID:
   - يجب أن يطابق Phone Number ID في `IntegrationAccount`
3. ✅ تحقق من Logs:
   ```bash
   python manage.py runserver
   # ستظهر جميع الطلبات
   ```
4. ✅ تحقق من أن WhatsApp Business Account نشط
5. ✅ أرسل رسالة من رقم مختلف (ليس من نفس رقم Business)

### المشكلة: Client لا يُنشأ

**الأعراض:**
- الرسالة تصل لكن Client لا يُنشأ

**الحلول:**
1. ✅ تحقق من وجود `IntegrationAccount` في قاعدة البيانات:
   ```python
   from integrations.models import IntegrationAccount
   IntegrationAccount.objects.filter(platform='whatsapp', status='connected')
   ```
2. ✅ تحقق من `phone_number_id` في metadata يطابق Phone Number ID من الرسالة
3. ✅ تحقق من Logs للأخطاء:
   ```bash
   python manage.py runserver
   # ستظهر أخطاء معالجة الرسالة
   ```

---

## 📚 المراجع

- [WhatsApp Business API Documentation](https://developers.facebook.com/docs/whatsapp)
- [Meta Webhooks Documentation](https://developers.facebook.com/docs/graph-api/webhooks)
- [دليل التكاملات الكامل](../READMEs/INTEGRATIONS_COMPLETE_GUIDE.md)
- [دليل اختبار الإشعارات](../READMEs/NOTIFICATION_TESTING_GUIDE.md)

---

## ✅ الخلاصة

**WhatsApp Webhook يحتاج:**

1. ✅ **Meta App** مع WhatsApp Product
2. ✅ **متغيرات البيئة**: `META_CLIENT_ID`, `META_CLIENT_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`
3. ✅ **Public URL** (ngrok للاختبار أو domain للإنتاج)
4. ✅ **Webhook URL** مُضاف في Meta App
5. ✅ **Subscription Fields**: `messages`
6. ✅ **IntegrationAccount** في قاعدة البيانات مع Phone Number ID
7. ✅ **Server يعمل** ويستقبل الطلبات

**الكود جاهز 100%** ✅ - فقط تحتاج إعداد Meta App وإضافة المتغيرات!

---

**آخر تحديث:** 2024

---

## Company WhatsApp (platform number)

Admin **Company WhatsApp** uses the same webhook URL. Subscribe `messages` on the WABA that owns `PLATFORM_WHATSAPP_PHONE_NUMBER_ID`.

If `metadata.phone_number_id` equals the platform PID, inbound messages go to the admin↔owner thread (`AdminTenantWhatsAppMessage`), not tenant CRM chat.

Do not connect the platform phone as a tenant WhatsApp account. See [COMPANY_WHATSAPP.md](./COMPANY_WHATSAPP.md).
