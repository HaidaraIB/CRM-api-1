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
