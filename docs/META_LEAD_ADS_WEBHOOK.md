# كيف يرسل فيسبوك (Meta) الليدز إلى الـ CRM ويتم تخزينها

## آلية العمل (باختصار)

1. **المستخدم** يملأ نموذج ليد (Lead Form) على فيسبوك أو إنستغرام ضمن إعلان Lead Ad.
2. **فيسبوك** يرسل إشعاراً فورياً (webhook) إلى عنوان الـ CRM.
3. **الـ CRM** يستقبل الطلب، يتحقق من التوقيع، يطلب تفاصيل الليد من Meta API، ثم ينشئ **Client** في قاعدة البيانات ويربطه بالشركة والحملة إن وُجدت.

---

## عنوان الـ Webhook (Callback URL)

يجب أن يكون عنوان الـ webhook **متاحاً من الإنترنت** (لا localhost إلا للاختبار مع أدوات مثل ngrok):

```
https://YOUR_DOMAIN/api/integrations/webhooks/meta/
```

مثال: `https://api.yourcrm.com/api/integrations/webhooks/meta/`

---

## ما الذي يحدث عند وصول ليد جديد؟

1. **فيسبوك** يرسل طلب `POST` إلى الـ URL أعلاه، والجسم يحتوي:
   - `entry[].changes[].field` = `"leadgen"`
   - `leadgen_id`, `form_id`, `page_id`

2. **الـ Backend** (`integrations/views.py` → `meta_webhook`):
   - يتحقق من توقيع الطلب (`X-Hub-Signature-256`) باستخدام `META_CLIENT_SECRET`.
   - يبحث عن **IntegrationAccount** (حساب Meta) المرتبط بنفس الـ `form_id` أو `page_id` (من `metadata.selected_form_id` أو `metadata.pages`).
   - يحصل على **Page Access Token** من الحساب.
   - يستدعي **Meta Graph API** (`get_lead_data`) لجلب تفاصيل الليد (الاسم، الهاتف، الإيميل، إلخ).
   - ينشئ **Client** في `crm.models.Client` مع:
     - `source='meta_lead_form'`
     - `campaign` من `metadata.form_campaign_mapping` إن رُبط النموذج بحملة
     - أرقام الهاتف في `ClientPhoneNumber`
   - إن كان **Auto-assign** مفعّلاً، يُعيَّن الليد لموظف.
   - يُسجَّل الحدث في `ClientEvent` و`IntegrationLog`.

---

## إعداد تطبيق فيسبوك (Meta for Developers)

حتى يرسل فيسبوك الليدز إلى الـ CRM يجب **اشتراك التطبيق في Webhooks لـ Page → leadgen**:

1. ادخل إلى [developers.facebook.com](https://developers.facebook.com) → تطبيقك.
2. من القائمة: **Webhooks** (أو **Use cases** → **Customize** بجانب **Webhooks**).
3. اختر **Page** (وليس User أو App).
4. في **Page** اضغط **Subscribe** أو **Edit subscription**.
5. عيّن:
   - **Callback URL**: `https://YOUR_DOMAIN/api/integrations/webhooks/meta/`
   - **Verify token**: نفس القيمة المخزنة في `.env` كـ `META_WEBHOOK_VERIFY_TOKEN`
6. في **Fields to subscribe** اختر **leadgen**.
7. احفظ. فيسبوك سيرسل طلب `GET` للتحقق (Challenge); الـ backend يرد بـ `hub.challenge` إذا كان `hub.verify_token` مطابقاً.

بعد ذلك، أي ليد جديد يصل على نموذج مرتبط بصفحة التطبيق سيُرسل إلى هذا الـ URL.

---

## متطلبات الـ Backend

- في **`.env`** (أو `settings`):
  - `META_CLIENT_ID`
  - `META_CLIENT_SECRET` (لتحقق توقيع الـ webhook)
  - `META_WEBHOOK_VERIFY_TOKEN` (نفس القيمة التي تُدخلها في واجهة Webhooks في فيسبوك)

- أن تكون الشركة قد:
  - ربطت **حساب Meta** (OAuth) من صفحة التكاملات.
  - اختارت **Lead Form** لتلك الصفحة عبر **Select Lead Form** (يُحفظ `form_id` و`page_id` في `account.metadata`).

عندها عند وصول حدث `leadgen` من فيسبوك، الـ backend يطابق الـ form/page مع الحساب والشركة ويُنشئ الليد تلقائياً في الـ CRM.
