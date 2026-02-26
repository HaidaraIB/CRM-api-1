# TikTok Integration – Lead Gen Only (مثل Meta Lead Form)

في هذا المشروع **TikTok = Lead Gen فقط**: استقبال ليدز من **TikTok Instant Form** فوراً في الـ CRM، مثل استقبال ليدز من **Meta Lead Form**. لا يوجد ربط حساب TikTok (OAuth) ولا بروفايل ولا فيديوهات.

---

## كيف يعمل

1. **الشركة تشترك بتكامل TikTok** من واجهة الـ CRM: **Integrations → TikTok**.
2. تظهر لها **رابط الويب هوك** الخاص بشركتها (مع `company_id`).
3. تنسخ الرابط وتسجّله في **TikTok Ads Manager** (Leads Center → CRM integration → TikTok Custom API with Webhooks).
4. عند تقديم أي مستخدم لاستمارة Instant Form مرتبطة بحملاتها، TikTok ترسل الليد إلى هذا الرابط.
5. الـ Backend يستقبل الطلب، يحدد الشركة، وينشئ **Client** جديد في الـ CRM تلقائياً (مصدر الليد = TikTok).

---

## ما تحتاجه

- **Backend:** إعداد `TIKTOK_LEADGEN_COMPANY_ID` أو `TIKTOK_LEADGEN_ADVERTISER_MAPPING` (أو استخدام `?company_id=` في الرابط). لا حاجة لـ `TIKTOK_CLIENT_ID` أو `TIKTOK_CLIENT_SECRET`.
- **Frontend:** من **Integrations → TikTok** يتم جلب رابط الويب هوك وعرضه مع زر نسخ وتوضيح بسيط.
- **TikTok for Business:** إنشاء حملات Lead Gen مع Instant Form، ثم ربط الـ CRM عبر الويب هوك (الرابط أعلاه).

---

## للمشتركين: ماذا أفعل لربط TikTok؟

**[دليل المشترك: ربط TikTok بالـ CRM](./TIKTOK_LEADGEN_SUBSCRIBER_GUIDE.md)** — خطوات بسيطة لمشتركي الـ CRM (نسخ الرابط من التكاملات → تسجيله في TikTok Ads Manager → إنشاء حملة Lead Gen). يمكن مشاركته مع العملاء.

## الدليل التقني الكامل (للمشغّلين)

**[دليل TikTok for Business – Lead Gen فقط](./TIKTOK_LEADGEN_TIKTOK_FOR_BUSINESS_GUIDE.md)** يشرح:

- ماذا يفعل الـ Backend (ويب هوك، إنشاء Client، توزيع تلقائي، سجل).
- إعداد الـ Backend (متغيرات البيئة، تحديد الشركة).
- **ما الذي تقوم به في TikTok for Business** خطوة بخطوة (Ads Manager، Instant Form، Leads Center، تسجيل رابط الويب هوك).
- الاختبار واستكشاف الأخطاء والمراجع الرسمية.

---

## واجهات الـ API ذات الصلة

| الطريقة | المسار | الوصف |
|---------|--------|--------|
| GET | `/api/integrations/accounts/tiktok-leadgen-config/` | إرجاع `webhook_url` و `company_id` للشركة الحالية (للعرض في Integrations → TikTok). |
| POST | `/api/integrations/webhooks/tiktok-leadgen/` | ويب هوك استقبال الليدز من TikTok (يُسجّل في TikTok Ads Manager). |

لا توجد واجهات OAuth أو بروفايل أو فيديوهات لـ TikTok في هذا المشروع.
