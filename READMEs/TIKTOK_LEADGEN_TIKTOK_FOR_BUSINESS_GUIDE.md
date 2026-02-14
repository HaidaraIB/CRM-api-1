# دليل TikTok for Business – Lead Gen فقط (استمارات Instant Form → CRM)

هذا الدليل يشرح **ما الذي تقوم به في TikTok for Business** (TikTok Ads Manager و Leads Center) لتفعيل جمع الليدز من Instant Form وإرسالها تلقائياً إلى الـ CRM عبر الويب هوك.

---

## 1. ما الذي ينجزه الـ Backend في الـ CRM؟

- **Endpoint ويب هوك:** `POST {API_BASE_URL}/api/integrations/webhooks/tiktok-leadgen/`  
  يستقبل طلبات TikTok عند كل ليد جديد، يرد دائماً **200 OK**.
- **تحليل الـ payload:** استخراج `lead_id`, `form_id`, `advertiser_id` وبيانات الليد (الاسم، الهاتف، البريد إن وُجدت) من أشكال متعددة (قائمة key/value أو كائن answers أو حقول مباشرة).
- **تحديد الشركة:**  
  - من query: `?company_id=1`  
  - أو من إعدادات التطبيق: `TIKTOK_LEADGEN_COMPANY_ID` أو `TIKTOK_LEADGEN_ADVERTISER_MAPPING`.
- **منع التكرار:** إذا وُجد سجل سابق لنفس `lead_id` لنفس الحساب، لا يُنشأ عميل مكرر.
- **إنشاء عميل (Client):** إنشاء `Client` في الـ CRM مع المصدر `tiktok`، وربطه بحساب تكامل "TikTok Lead Gen" للشركة، وإضافة رقم الهاتف في `ClientPhoneNumber` إن وُجد.
- **التوزيع التلقائي:** إذا كانت إعدادات الشركة تفعّل التوزيع التلقائي، يُعيَّن الليد تلقائياً لموظف (Round Robin).
- **السجل:** كل استلام ليد يُسجّل في `IntegrationLog` (نجاح أو فشل).

لا حاجة لأي تكامل آخر (مثل Login Kit أو ربط حساب TikTok شخصي) لاستقبال الليدز.

---

## 2. إعداد الـ Backend (مرة واحدة)

### 2.1 متغيرات البيئة (.env)

اختر **أحد** الخيارين:

**خيار A – شركة واحدة (كل الليدز لهذه الشركة):**

```env
TIKTOK_LEADGEN_COMPANY_ID=1
```

استبدل `1` بمعرّف الشركة (Company ID) في قاعدة البيانات.

**خيار B – أكثر من شركة (ربط حسب advertiser_id من TikTok):**

```env
TIKTOK_LEADGEN_ADVERTISER_MAPPING={"1234567890": "1", "0987654321": "2"}
```

- المفتاح = `advertiser_id` من TikTok (حساب المعلن).
- القيمة = `company_id` في الـ CRM.

يمكنك معرفة `advertiser_id` من TikTok Ads Manager (إعدادات الحساب أو الـ API).

**اختياري – استخدام query بدل الإعدادات:**

إذا لم تضبط المتغيرين أعلاه، يمكنك تسجيل ويب هوك مختلف لكل شركة:

- شركة 1: `https://your-api.com/api/integrations/webhooks/tiktok-leadgen/?company_id=1`
- شركة 2: `https://your-api.com/api/integrations/webhooks/tiktok-leadgen/?company_id=2`

(تأكد أن TikTok تسمح بـ query parameters على عنوان الويب هوك؛ إن لم تسمح، استخدم خيار A أو B.)

### 2.2 التأكد من تشغيل الـ API

- الـ endpoint يجب أن يكون متاحاً عبر **HTTPS** في بيئة الإنتاج (TikTok لا تقبل http للويب هوك).
- للتطوير المحلي يمكن استخدام نفق (مثل ngrok) بعنوان `https://...` وتسجيله مؤقتاً في TikTok.

---

## 3. ما الذي تقوم به في TikTok for Business (خطوة بخطوة)

كل الخطوات التالية تتم من **TikTok Ads Manager** و **Leads Center** (TikTok for Business)، وليس من TikTok for Developers (Login Kit).

### 3.1 إنشاء / الدخول إلى TikTok for Business و Ads Manager

1. ادخل إلى: **[https://ads.tiktok.com](https://ads.tiktok.com)**  
2. سجّل الدخول أو أنشئ **حساب TikTok for Business**.  
3. أنشئ أو ادخل إلى **TikTok Ads Manager** (وحساب إعلانات واحد على الأقل).  
4. إذا لم يكن لديك Business Center أو Ad Account، أنشئهما من القائمة/الإعدادات المناسبة.

### 3.2 إنشاء استمارة Instant Form (جمع الليدز)

1. من TikTok Ads Manager: **Assets** → **Instant Pages** (أو **Tools** → **Instant Page** حسب الواجهة).  
2. اضغط **Create** لإنشاء **Instant Form** جديد.  
3. اختر قالب الاستمارة (مثلاً Blank أو Rich Content).  
4. أضف الحقول التي تريد جمعها، مثلاً:  
   - **Full name** (أو Name)  
   - **Phone** (أو Phone number)  
   - **Email** (اختياري)  
5. املأ **Business description** و **Privacy Policy** و **Call-to-action** كما يطلب TikTok.  
6. احفظ الاستمارة وانتظر الموافقة إن لزم (قد تستغرق حتى 24 ساعة).

الـ CRM يتوقع مفاتيح مثل: اسم (full name / name)، هاتف (phone / phone_number / mobile)، وبريد (email). إذا استخدمت أسماء حقول قريبة من هذه، الـ backend سيستخرجها تلقائياً.

### 3.3 إنشاء حملة Lead Generation واستخدام الاستمارة

1. من TikTok Ads Manager: **Campaign** → **Create** (أو **Create campaign**).  
2. اختر الهدف **Lead generation**.  
3. في مرحلة إنشاء الإعلان (Ad level)، اختر **Instant Form** كـ **Destination** واختر الاستمارة التي أنشأتها.  
4. أكمِل إنشاء الحملة (استهداف، ميزانية، إبداع، إلخ) ثم انشرها.

بعد تفعيل الحملة، أي مستخدم يملأ الاستمارة يُحسب كـ "Lead" ويمكن إرساله إلى الـ CRM عبر الويب هوك.

### 3.4 فتح Leads Center وتفعيل استقبال الليدز

1. من TikTok Ads Manager اذهب إلى **Tools** أو **Assets** ثم **Leads Center** (أو **Lead Center**).  
2. في Leads Center ستجد قائمة **Instant Forms** و/أو **Leads** المرتبطة بحسابك.

### 3.5 ربط الـ CRM عبر “TikTok Custom API with Webhooks”

1. داخل **Leads Center** ابحث عن قسم **CRM integration** أو **Integrations** أو **Connect CRM**.  
2. اختر **TikTok Custom API with Webhooks** (أو ما يعادله: تكامل مخصص / Custom / Webhook).  
3. في حقل **Webhook URL** (أو **Callback URL**) أدخل عنوان الـ CRM بالكامل، مثلاً:  
   - إنتاج: `https://your-api-domain.com/api/integrations/webhooks/tiktok-leadgen/`  
   - مع شركة محددة (إن دعمته واجهة TikTok):  
     `https://your-api-domain.com/api/integrations/webhooks/tiktok-leadgen/?company_id=1`  
4. احفظ الإعدادات وفعّل الاشتراك/التكامل إن وجد خيار تفعيل.  
5. تأكد أن **Form subscription** أو خيار “إرسال الليدز إلى ويب هوك” مفعّل للاستمارات التي تريدها.

ملاحظة: واجهة TikTok تتغير أحياناً. إن لم تجد "Custom API with Webhooks" تحت Leads Center، راجع:  
**[Available CRM integrations for TikTok Lead Generation](https://ads.tiktok.com/help/article/available-crm-integrations-tiktok-lead-generation)** و **[How to troubleshoot webhooks](https://ads.tiktok.com/help/article/troubleshoot-webhooks)**.

### 3.6 (اختياري) الحصول على Advertiser ID للربط بعدة شركات

إذا كنت تستخدم **TIKTOK_LEADGEN_ADVERTISER_MAPPING**:

1. من TikTok Ads Manager: **Settings** أو **Business Center** → **Accounts** أو **Ad accounts**.  
2. أو من وثائق/واجهة **TikTok Marketing API**: استخدم الـ API لسرد Advertisers (حسابات المعلنين).  
3. استخدم هذا المعرّف كمفتاح في `TIKTOK_LEADGEN_ADVERTISER_MAPPING` والقيمة هي `company_id` في الـ CRM.

---

## 4. اختبار التكامل

1. **تأكد من الإعداد:**  
   - `TIKTOK_LEADGEN_COMPANY_ID` أو `TIKTOK_LEADGEN_ADVERTISER_MAPPING` أو `?company_id=` مضبوط.  
   - الـ API يعمل و الـ URL متاح عبر HTTPS.  
2. من TikTok Ads Manager أو Leads Center، إن أمكن، استخدم أي خيار **اختبار ويب هوك** أو **إرسال ليد تجريبي** إن وُجد.  
3. أو قدّم استمارة حقيقية من إعلانك (من حساب اختبار أو حملة فعلية).  
4. تحقق في الـ CRM:  
   - ظهور عميل جديد (Leads/Clients) بالمصدر **TikTok**.  
   - في **Integration Logs** (إن وُجدت في واجهتك) سجل `tiktok_lead_received` ناجح.

إن لم يظهر العميل، راجع سجلات الـ Backend (logs) و **استكشاف الأخطاء** أدناه.

---

## 5. استكشاف الأخطاء

| المشكلة | ما التحقق منه |
|--------|-----------------|
| لا يصل أي طلب للـ backend | التأكد من تسجيل الـ URL الصحيح في Leads Center، وأن الـ endpoint يعمل على HTTPS. |
| يصل الطلب لكن لا يُنشأ عميل | تحقق من الـ logs: إن ظهر "could not parse payload" فغالباً شكل الـ payload مختلف؛ إن ظهر "no company_id" فاضبط الشركة (env أو query). |
| عميل مكرر | الـ backend يمنع التكرار حسب `lead_id`؛ تأكد أن TikTok ترسل `lead_id` في الـ payload. |
| الشركة غير صحيحة | عند استخدام أكثر من شركة: تحقق من `TIKTOK_LEADGEN_ADVERTISER_MAPPING` أو من استخدام `?company_id=` الصحيح. |

---

## 6. مراجع TikTok الرسمية

- [About TikTok Leads Center](https://ads.tiktok.com/help/article/about-tiktok-leads-center)  
- [Set up lead generation with Instant Form](https://ads.tiktok.com/help/article/set-up-lead-generation-with-instant-form)  
- [Access leads data on Instant Forms](https://ads.tiktok.com/help/article/access-leads-data-on-instant-forms)  
- [Available CRM integrations (incl. Custom API with Webhooks)](https://ads.tiktok.com/help/article/available-crm-integrations-tiktok-lead-generation)  
- [How to troubleshoot webhooks](https://ads.tiktok.com/help/article/troubleshoot-webhooks)  
- [TikTok Marketing API (API for Business)](https://business-api.tiktok.com/) – للمطورين الذين يريدون جلب الليدز برمجياً لاحقاً.

---

## 7. ملخص سريع

1. **في الـ CRM (Backend):**  
   ضبط `TIKTOK_LEADGEN_COMPANY_ID` أو `TIKTOK_LEADGEN_ADVERTISER_MAPPING` (أو استخدام `?company_id=`)، والتأكد من نشر الـ API على HTTPS.  
2. **في TikTok for Business:**  
   إنشاء حساب إعلانات → إنشاء Instant Form → إنشاء حملة Lead Gen وربط الاستمارة → Leads Center → CRM integration → TikTok Custom API with Webhooks → تسجيل ويب هوك الـ CRM.  
3. بعد ذلك، كل ليد جديد من الاستمارة يُرسل إلى الـ CRM ويُحوّل تلقائياً إلى عميل (Client) مع المصدر TikTok.

هذا الدليل يغطي **Lead Gen فقط** ولا يتطلب تفعيل Login Kit أو ربط حساب TikTok الشخصي في الـ CRM.
