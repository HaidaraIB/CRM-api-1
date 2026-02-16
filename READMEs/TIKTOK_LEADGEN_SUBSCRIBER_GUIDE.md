# دليل المشترك: ربط TikTok بالـ CRM (استقبال الليدز فوراً)

هذا الدليل **لمشتركي الـ CRM** (الشركات): كيف تفعّل استقبال ليدز TikTok Instant Form في حسابك بحيث تصل كل ليد كعميل جديد في الـ CRM تلقائياً.

---

## ما الذي ستحصل عليه؟

- عند إعداد الربط، كل مستخدم يملأ **استمارة Instant Form** في إعلان TikTok خاص بك → تُرسل البيانات إلى الـ CRM فوراً.
- يظهر العميل في قائمة العملاء/الليدز بالمصدر **TikTok**، مع الاسم ورقم الهاتف (والبريد إن أضفته في الاستمارة).
- إذا كان التوزيع التلقائي مفعّلاً في إعدادات شركتك، يُعيَّن الليد تلقائياً لأحد الموظفين.

---

## الخطوات (ما الذي تقوم به أنت)

### 1. في الـ CRM: الحصول على رابط الويب هوك

1. ادخل إلى **التكاملات** (Integrations) من القائمة.
2. اختر تبويب **TikTok**.
3. ستظهر لك بطاقة **TikTok Lead Gen** ورابط طويل (Webhook URL). هذا الرابط خاص بشركتك فقط.
4. اضغط **نسخ** (Copy) لنسخ الرابط.

هذا الرابط سيُستخدم في TikTok Ads Manager في الخطوة التالية.

---

### 2. في TikTok: إنشاء استمارة جمع الليدز (Instant Form)

1. ادخل إلى **[TikTok Ads Manager](https://ads.tiktok.com)** وسجّل الدخول بحساب TikTok for Business.
2. من القائمة: **Assets** → **Instant Pages** (أو **Tools** → **Instant Page**).
3. اضغط **Create** لإنشاء **Instant Form** جديد.
4. اختر قالب الاستمارة وأضف الحقول التي تريدها، مثلاً:
   - **Full name** (الاسم)
   - **Phone** (رقم الهاتف)
   - **Email** (اختياري)
5. املأ **Business description** و **Privacy Policy** و **Call-to-action** كما يطلب TikTok، ثم احفظ الاستمارة.

---

### 3. في TikTok: ربط الـ CRM (تسجيل رابط الويب هوك)

1. من TikTok Ads Manager اذهب إلى **Leads Center** (أحياناً تحت **Tools** أو **Assets**).
2. ابحث عن **CRM integration** أو **Integrations** أو **Connect CRM**.
3. اختر **TikTok Custom API with Webhooks** (أو **Custom** / **Webhook**).
4. في حقل **Webhook URL** (أو **Callback URL**) الصق الرابط الذي نسخته من الـ CRM (الخطوة 1).
5. احفظ الإعدادات وفعّل التكامل إن وجد خيار تفعيل.

بعد ذلك، TikTok سترسل كل ليد جديد إلى هذا الرابط، والـ CRM سيتلقاه ويُنشئ عميلاً تلقائياً.

---

### 4. إنشاء حملة إعلانية Lead Generation

1. من TikTok Ads Manager: **Campaign** → **Create** (أو **Create campaign**).
2. اختر الهدف **Lead generation**.
3. في مرحلة الإعلان (Ad level)، اختر **Instant Form** كـ **Destination** واختر الاستمارة التي أنشأتها.
4. أكمِل إنشاء الحملة (استهداف، ميزانية، إبداع) ثم انشرها.

من الآن فصاعداً، كل من يملأ الاستمارة من إعلانك سيظهر كعميل جديد في الـ CRM.

---

## ملخص سريع

| أين | ماذا تفعل |
|-----|------------|
| **الـ CRM** | التكاملات → TikTok → نسخ رابط الويب هوك |
| **TikTok Ads Manager** | Leads Center → CRM integration → Custom API with Webhooks → لصق الرابط |
| **TikTok Ads Manager** | إنشاء Instant Form ثم حملة Lead Gen مرتبطة بالاستمارة |

---

## استكشاف الأخطاء

- **الليدز لا تصل للـ CRM:** تأكد أنك نسخت الرابط كاملاً من صفحة التكاملات وأنك لصقته في TikTok دون حذف أي جزء. تأكد أن الرابط يبدأ بـ `https://`.
- **عميل مكرر:** الـ CRM يمنع التكرار تلقائياً حسب معرّف الليد من TikTok.
- **لا أجد Leads Center أو CRM integration:** واجهة TikTok قد تختلف قليلاً. راجع مساعدة TikTok: [Available CRM integrations](https://ads.tiktok.com/help/article/available-crm-integrations-tiktok-lead-generation).

---

**ملاحظة:** لا تحتاج لربط حساب TikTok الشخصي أو تسجيل الدخول عبر TikTok في الـ CRM. التكامل يعتمد فقط على **رابط الويب هوك** الذي تنسخه من التكاملات وتسجّله في TikTok Ads Manager.
