# نظام التظاهر (Impersonation)

يسمح لـ **Super Admin** بتسجيل الدخول كمالك شركة (Company Owner) لدعم العملاء أو مراجعة الواجهة من وجهة نظر المستخدم.

## الـ API

### بدء التظاهر (للمدير فقط)

- **POST** `/api/auth/impersonate/`
- **الصلاحيات:** مصادقة + Super Admin
- **الجسم:** إما `{ "company_id": <id> }` أو `{ "user_id": <company_owner_id> }`
- **الاستجابة:**
  - `access`, `refresh`: توكنات JWT للمستخدم المظاهر
  - `user`: بيانات المستخدم (مالك الشركة)
  - `impersonated_by`: بيانات الـ Super Admin الذي نفّذ التظاهر
  - `impersonation_code`: كود لمرة واحدة صالح لمدة 120 ثانية لاستخدامه في تطبيق CRM

### استبدال الكود بتوكنات (تطبيق CRM)

- **GET** `/api/auth/impersonate-exchange/?code=<impersonation_code>`
- **الصلاحيات:** لا يتطلب مصادقة (AllowAny)
- **الاستجابة:** `{ "access", "refresh", "user" }` — الكود يُبطَل بعد الاستخدام.

## سجل التدقيق

كل عملية تظاهر تُسجَّل في **System Audit Log** (`settings.SystemAuditLog`):

- **action:** `impersonation_start`
- **message:** وصف من يقوم بالتظاهر ومن هو المستخدم المستهدف
- **metadata:** `target_user_id`, `target_username`, `target_email`, `company_id`, `company_name`
- **actor:** المستخدم Super Admin
- **ip_address:** عنوان IP الطلب

## لوحة الإدارة (Admin Panel)

- في صفحة **الشركات (Tenants)** يظهر زر **"الدخول كمالك الشركة"** فقط لـ Super Admin.
- عند الضغط يُطلب تأكيد ثم يتم استدعاء الـ API مع `company_id`.
- إذا كان **VITE_CRM_APP_URL** مضبوطاً، تُفتح نافذة جديدة لتطبيق CRM مع `?code=...` ويتم تبادل الكود تلقائياً وتسجيل الدخول كمالك الشركة.

## تطبيق CRM (CRM-project)

- المسار **/impersonate?code=...** يستقبل الكود، يستدعي `impersonate-exchange`، يخزّن التوكنات والمستخدم، ثم يحوّل إلى لوحة التحكم.

## التكوين

- **Admin Panel:** تعيين `VITE_CRM_APP_URL` إلى عنوان تطبيق CRM (مثلاً `https://crm.example.com`) لفتح التطبيق في نافذة جديدة بعد التظاهر.
- **CRM (production):** التأكد من أن `VITE_API_URL` يشير إلى جذر الـ API (مثلاً `https://api.example.com/api`). إن كان يشير إلى الدومين فقط (`https://api.example.com`) فسيتم إلحاق `/api` تلقائياً لطلب `impersonate-exchange`.

## استكشاف الأخطاء (Production 404)

إذا ظهر في السجلات **"Not Found: /api/auth/impersonate-exchange/"** أو واجهة "Invalid or expired code" على الـ VPS:

### 1. التحقق من النشر (Diagnostic endpoint)

من المتصفح أو `curl` على السيرفر نفسه:

```bash
curl -s "https://<YOUR_API_DOMAIN>/api/auth/impersonate-exchange/status/"
```

- إذا حصلت على **200** و `{"status":"ok","endpoint":"impersonate-exchange"}` فالمسارات منشورة، والمشكلة غالباً كود منتهي أو Cache (راجع البند 3).
- إذا حصلت على **404** فالمسارات غير منشورة: انسخ آخر نسخة من المشروع إلى الـ VPS وأعد تشغيل الخدمة (البند 2).

### 2. التأكد من نشر الكود وإعادة التشغيل

1. رفع آخر نسخة من الكود (التي تحتوي على `impersonate_exchange` و `impersonate_exchange_status` ومساراتها في `crm_saas_api/urls.py`).
2. إعادة تشغيل خدمة Django (مثلاً: `sudo systemctl restart gunicorn` أو إعادة تشغيل العملية التي تشغّل المشروع).
3. إعادة المحاولة و/أو استدعاء `/api/auth/impersonate-exchange/status/` مرة أخرى.

### 3. إذا كان الـ status يعمل لكن التظاهر يفشل

- تأكد أن طلب التظاهر يصل إلى `https://<your-api-domain>/api/auth/impersonate-exchange/?code=...`.
- الكود صالح لمدة **120 ثانية** فقط؛ إذا فتحت الرابط بعد تأخير طويل سيظهر "Invalid or expired code." — أعد التظاهر من لوحة الإدارة.
- على الـ VPS، إذا كان Cache (مثلاً Redis أو Memcached) مختلفاً عن بيئة التطوير أو غير مضبوط، قد لا يُحفظ الكود: تأكد من إعدادات `CACHES` في الإنتاج.
