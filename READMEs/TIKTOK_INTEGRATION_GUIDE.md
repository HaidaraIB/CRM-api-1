# 📘 دليل تكامل TikTok مع الـ CRM (2026)

> دليل خطوة بخطوة لربط حسابات TikTok مع نظام الـ CRM باستخدام TikTok Login Kit (OAuth 2.0).

---

## 📑 جدول المحتويات

1. [نظرة عامة](#-نظرة-عامة)
2. [المتطلبات](#-المتطلبات)
3. [إنشاء تطبيق TikTok (TikTok for Developers)](#-إنشاء-تطبيق-tiktok-tiktok-for-developers)
4. [إعداد الـ Backend (CRM-api-1)](#-إعداد-الـ-backend-crm-api-1)
5. [إعداد الـ Frontend (CRM-project)](#-إعداد-الـ-frontend-crm-project)
6. [تدفق OAuth من البداية للنهاية](#-تدفق-oauth-من-البداية-للنهاية)
7. [واجهات TikTok في الـ CRM (API + Webhook)](#-واجهات-tiktok-في-الـ-crm-api--webhook)
8. [الليدز من TikTok (Instant Form)](#-الليدز-من-tiktok-instant-form)
9. [الاختبار](#-الاختبار)
10. [النشر (Production)](#-النشر-production)
11. [استكشاف الأخطاء](#-استكشاف-الأخطاء)
12. [المراجع الرسمية](#-المراجع-الرسمية)

---

## 📋 نظرة عامة

### ما الذي يوفره التكامل؟

- **ربط حساب TikTok** بالمؤسسة في الـ CRM (كل شركة يمكنها ربط حساب أو أكثر).
- **تسجيل الدخول عبر TikTok (OAuth 2.0)** مع دعم PKCE.
- **حفظ Access Token و Refresh Token** (مشفّرين) واستخدامهما لاستدعاء TikTok APIs.
- **تجديد الـ Token تلقائياً** قبل انتهاء الصلاحية (Access token 24 ساعة، Refresh token 365 يوم).

### كل ما يمكن الحصول عليه من TikTok للـ CRM (في هذا المشروع)

| الميزة | الوصف | الـ Scope / المصدر |
|--------|--------|---------------------|
| **معلومات الحساب الأساسية** | الاسم، الصورة، المعرف (open_id) | `user.info.basic` |
| **البروفايل الموسّع** | الرابط، البيو، التوثيق (is_verified) | `user.info.profile` |
| **إحصائيات الحساب** | عدد المتابعين، المتابَعين، الإعجابات، عدد الفيديوهات | `user.info.stats` |
| **قائمة الفيديوهات** | فيديوهات الحساب العامة مع pagination (عنوان، غلاف، مشاهدات، إعجابات، تعليقات) | `video.list` |
| **مزامنة البروفايل** | تحديث كل البيانات أعلاه عند الضغط على Sync | نفس الـ Scopes |
| **ويب هوك إلغاء التفويض** | عند إلغاء المستخدم ربط التطبيق من TikTok يتم تحديث الحساب تلقائياً إلى disconnected | Webhook `authorization.removed` |
| **سجل أحداث الفيديو** | تسجيل أحداث رفع/نشر فيديو (للتدقيق) | Webhook `video.upload.failed`, `video.publish.completed` |

**هل يمكن الحصول على ليدز من TikTok؟** نعم. للتركيز على **Lead Gen فقط** (بدون Login Kit): [دليل TikTok for Business – Lead Gen فقط](./TIKTOK_LEADGEN_TIKTOK_FOR_BUSINESS_GUIDE.md). للشرح داخل هذا الملف انظر [الليدز من TikTok (Instant Form)](#-الليدز-من-tiktok-instant-form).

### البنية في المشروع

| المكوّن | المسار | الوظيفة |
|--------|--------|---------|
| Backend OAuth | `integrations/oauth_utils.py` | `TikTokOAuth`: التفويض، التوكن، جلب user info كامل، `list_videos()` |
| Backend Views | `integrations/views.py` | `connect`, `oauth_callback`, `disconnect`, `sync`, `tiktok_profile`, `tiktok_videos`, ويب هوك TikTok |
| Backend Webhook | `integrations/views.py` + `urls.py` | `tiktok_webhook`: استقبال `authorization.removed` وحدث الفيديو |
| Backend Settings | `crm_saas_api/settings.py` | `TIKTOK_CLIENT_ID`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` |
| Frontend صفحة التكاملات | `pages/IntegrationsPage.tsx` | عرض حسابات TikTok، Connect / Sync / View profile / Disconnect / Edit |
| Frontend API | `services/api.ts` | `getConnectedAccountsAPI`, `createConnectedAccountAPI`, `connectIntegrationAccountAPI`, `syncIntegrationAccountAPI`, `getTikTokProfileAPI`, `getTikTokVideosAPI` |

---

## 🔌 واجهات TikTok في الـ CRM (API + Webhook)

### واجهات الـ API (تحتاج مصادقة المستخدم)

| الطريقة | المسار | الوصف |
|---------|--------|--------|
| POST | `/api/integrations/accounts/{id}/connect/` | بدء OAuth والحصول على رابط التفويض |
| POST | `/api/integrations/accounts/{id}/sync/` | مزامنة البروفايل والإحصائيات من TikTok |
| GET | `/api/integrations/accounts/{id}/tiktok-profile/` | جلب البروفايل الكامل (اسم، صورة، إحصائيات، رابط، بيو) |
| GET | `/api/integrations/accounts/{id}/tiktok-videos/?cursor=&max_count=20` | قائمة فيديوهات الحساب مع pagination |
| POST | `/api/integrations/accounts/{id}/disconnect/` | قطع الربط وحذف التوكنات |

### ويب هوك TikTok (Login Kit)

- **URL:** `POST {API_BASE_URL}/api/integrations/webhooks/tiktok/`
- **الاستخدام:** تسجيل هذا الرابط في TikTok Developer Portal (قسم Webhook) لاستقبال الأحداث.
- **الأحداث المعالجة:**
  - `authorization.removed`: عند إلغاء المستخدم التفويض يتم تحديث الحساب إلى `disconnected` تلقائياً.
  - `video.upload.failed` / `video.publish.completed`: تسجيل الحدث في `IntegrationLog` للتدقيق.
- يجب الرد دائماً بـ **200 OK** خلال استلام الطلب حتى لا تعيد TikTok المحاولة.

### ويب هوك TikTok Lead Gen (Instant Form)

- **URL:** `POST {API_BASE_URL}/api/integrations/webhooks/tiktok-leadgen/`
- **الاستخدام:** تسجيل هذا الرابط في **TikTok Ads Manager** (Leads Center → CRM integration → TikTok Custom API with Webhooks) لاستقبال ليدز استمارات Instant Form.
- الرد دائماً **200 OK**؛ الطلب يُسجّل في السجلات. لإنشاء تلقائي لـ Lead (Client) في الـ CRM راجع قسم [الليدز من TikTok](#-الليدز-من-tiktok-instant-form).

---

## 📥 الليدز من TikTok (Instant Form)

**نعم، يمكنك استقبال ليدز من TikTok** (استمارات Instant Form في إعلانات TikTok)، لكن آلية الاستلام مختلفة عن Login Kit.

### الفرق بين التكاملين

| | Login Kit (الحالي) | Lead Gen (Instant Form) |
|---|---------------------|---------------------------|
| **الغرض** | ربط حساب مستخدم (بروفايل، فيديوهات) | استقبال ليدز من إعلانات Lead Generation |
| **المصادقة** | TikTok for Developers (OAuth مستخدم) | TikTok **Marketing API** (OAuth **معلن / Business**) |
| **المنصة** | developers.tiktok.com + open.tiktokapis.com | **TikTok Ads Manager** + business-api.tiktok.com |
| **استلام الليدز** | — | ويب هوك أو Marketing API (جلب الليدز) |

### كيف تحصل على الليدز في الـ CRM

1. **ويب هوك (مُفضّل للوقت الفعلي)**  
   TikTok توفّر **TikTok Custom API with Webhooks**: يمكنك تسجيل عنوان ويب هوك خاص بك في **TikTok Ads Manager** (Leads Center → CRM integration) لاستقبال كل ليد جديد فور تقديم الاستمارة.  
   - في هذا المشروع يوجد endpoint جاهز لاستقبال الطلبات:  
     `POST {API_BASE_URL}/api/integrations/webhooks/tiktok-leadgen/`  
   - يرد دائماً بـ **200 OK** ويُسجّل الطلب في السجلات. يمكن لاحقاً ربط المنطق بإنشاء **Client** في الـ CRM (مثل ما يحدث مع Meta Lead Forms).  
   - للتفعيل الكامل تحتاج: حساب TikTok for Business، تفعيل Lead Gen في الحملات، وإعداد **TikTok Marketing API** (تسجيل التطبيق، OAuth للمعلنين، وتسجيل عنوان الويب هوك في Leads Center).

2. **استدعاء Marketing API (جلب الليدز)**  
   بدلاً من الويب هوك يمكنك استخدام **TikTok Marketing API** (business-api.tiktok.com) لـ:  
   - سرد نماذج Lead Gen (`lead form` list)،  
   - جلب الليدز حسب النموذج أو الفترة (مثلاً endpoint من نوع `lead/get/` أو ما يكافئه في الوثائق الحالية).  
   يتطلب ذلك نفس مصادقة المعلن (Access Token من OAuth TikTok for Business).

3. **التكاملات الجاهزة**  
   إن لم ترد بناء ويب هوك خاص بك، يمكن ربط TikTok Lead Gen بـ: HubSpot، Salesforce، Klaviyo، Google Sheets، Zapier، LeadsBridge، ثم استيراد الليدز إلى الـ CRM أو مزامنتها عبر Zapier/LeadsBridge.

### ما هو مُنجز في المشروع الآن

- **Endpoint ويب هوك لاستقبال ليدز TikTok:**  
  `POST /api/integrations/webhooks/tiktok-leadgen/`  
  - يقبل الطلبات، يرد **200**، ويُسجّل الـ body في السجلات (للتطوير والربط لاحقاً بإنشاء Client).  
- **التوثيق:** هذا القسم + [TikTok Marketing API](https://ads.tiktok.com/marketing_api/docs) و[الوصول لليدز في Instant Forms](https://ads.tiktok.com/help/article/access-leads-data-on-instant-forms).

لتفعيل **إنشاء تلقائي لـ Lead (Client)** عند استلام ويب هوك الليد من TikTok، تحتاج إلى: (1) معرفة شكل الـ payload من وثائق TikTok Marketing API / Lead Gen، (2) ربط الليد بحساب إعلانات/شركة في الـ CRM إن أمكن، (3) استخراج الاسم/الهاتف/البريد من الـ payload وإنشاء `Client` كما في ويب هوك Meta.

---

## ✅ المتطلبات

- حساب **TikTok for Developers** (بريد ورقم هاتف للتسجيل).
- **Backend** يعمل (Django) مع تطبيق `integrations` مفعّل.
- **Frontend** يعمل (React/Vite) مع صفحة Integrations.
- في الإنتاج: دومين **HTTPS** لـ API ولوحة TikTok (TikTok يتطلب `https` للـ Redirect URI في Production).

---

## 🔧 إنشاء تطبيق TikTok (TikTok for Developers)

### الخطوة 1: التسجيل والوصول إلى لوحة التطبيقات

1. اذهب إلى: **https://developers.tiktok.com/**
2. سجّل الدخول أو أنشئ حساب مطوّر من: **https://developers.tiktok.com/signup**
3. من القائمة العلوية: **البروفايل (أيقونة المستخدم)** → **Manage apps** → **https://developers.tiktok.com/apps**

### الخطوة 2: إنشاء تطبيق جديد

1. اضغط **Connect an app** (أو ما يعادله في الواجهة الحالية).
2. اختر **مالك التطبيق**: منظمة (Organization) إن وُجدت، أو حسابك الشخصي.
3. أدخل **اسم التطبيق** (مثل: `My CRM`) و**الوصف** و**الفئة** حسب ما يطلب الموقع.
4. في **Platforms** اختر **Web** (وإن احتجت لاحقاً Android/iOS أضفها).
5. احفظ وإنهِ إنشاء التطبيق.

### الخطوة 3: الحصول على Client Key و Client Secret

1. من صفحة التطبيق، اذهب إلى **App details** (أو **Credentials**).
2. **Client key** = هذا هو `TIKTOK_CLIENT_ID` في الـ Backend.
3. **Client secret** = هذا هو `TIKTOK_CLIENT_SECRET` (احفظه في مكان آمن ولا تشاركه).

### الخطوة 4: إضافة منتج Login Kit

1. في نفس صفحة التطبيق، ابحث عن **Products** واضغط **Add products**.
2. اختر **Login Kit** وأضفه للتطبيق.
3. بعد الإضافة ستظهر إعدادات **Login Kit** (بما فيها Redirect URIs للويب).

### الخطوة 5: تسجيل Redirect URI للويب

**مهم جداً:** الـ Redirect URI يجب أن يطابق بالضبط ما يستخدمه الـ Backend.

- يجب أن يبدأ بـ **`https`** (في Production).  
- للتطوير المحلي، TikTok يسمح أحياناً بـ `http://localhost` حسب السياسة الحالية؛ إن لم يقبل، استخدم نفق مثل **ngrok** وادخل رابط `https` من ngrok.
- لا يُسمح بوجود **query parameters** أو **fragment (#)** داخل الـ URI المسجّل.
- أقصى عدد URIs مسجّلة غالباً 10.

**القيمة التي يستخدمها الـ Backend (من `settings.py`):**

```
{API_BASE_URL}/api/integrations/accounts/oauth/callback/tiktok/
```

**أمثلة:**

| البيئة | قيمة `API_BASE_URL` | Redirect URI المسجّل في TikTok |
|--------|---------------------|----------------------------------|
| تطوير محلي | `http://localhost:8000` | `http://localhost:8000/api/integrations/accounts/oauth/callback/tiktok/` |
| إنتاج | `https://api.yourdomain.com` | `https://api.yourdomain.com/api/integrations/accounts/oauth/callback/tiktok/` |

1. في إعدادات **Login Kit** → **Web** → **Redirect URI**.
2. أضف الـ URI أعلاه (واحد للتطوير وواحد للإنتاج إن لزم).
3. احفظ التغييرات.

### الخطوة 6: Scopes (الصلاحيات)

الـ Backend يطلب افتراضياً كل الـ Scopes المفيدة للـ CRM:

| Scope | الوظيفة |
|-------|---------|
| `user.info.basic` | الاسم، الصورة، المعرف |
| `user.info.profile` | الرابط، البيو، التوثيق |
| `user.info.stats` | المتابعون، المتابَعون، الإعجابات، عدد الفيديوهات |
| `video.list` | قائمة الفيديوهات العامة للحساب |

في لوحة TikTok: **Scopes** → Add Scopes → أضف الأربعة أعلاه واحفظ.

### الخطوة 7: Sandbox vs Production

- **Sandbox:** للاختبار دون مراجعة TikTok؛ قد يكون الوصول محدوداً.
- **Production:** يتطلب تقديم التطبيق للمراجعة (App Review) وفق [إرشادات TikTok](https://developers.tiktok.com/doc/app-review-guidelines).

للتجربة السريعة استخدم Sandbox؛ للاستخدام الفعلي قدّم التطبيق للمراجعة وانتظر الموافقة.

---

## 🔧 إعداد الـ Backend (CRM-api-1)

### 1. متغيرات البيئة (.env)

في مجلد المشروع `CRM-api-1` أنشئ أو حدّث ملف `.env`:

```env
# TikTok OAuth (من لوحة TikTok for Developers)
TIKTOK_CLIENT_ID=your_client_key_here
TIKTOK_CLIENT_SECRET=your_client_secret_here

# عنوان الـ API (يُستخدم لبناء Redirect URI تلقائياً)
# تطوير:
API_BASE_URL=http://localhost:8000
# إنتاج:
# API_BASE_URL=https://api.yourdomain.com

# مفتاح تشفير التوكنات (مطلوب للتكاملات)
# إنشاؤه: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
INTEGRATION_ENCRYPTION_KEY=your_fernet_key_here

# عنوان الواجهة الأمامية (بعد الربط يُعاد التوجيه هنا)
FRONTEND_URL=http://localhost:3000
# إنتاج: FRONTEND_URL=https://app.yourdomain.com

# إعدادات Django الأساسية
SECRET_KEY=your_django_secret_key
DEBUG=True
```

- استبدل `your_client_key_here` و`your_client_secret_here` من خطوة «الحصول على Client Key و Client Secret».
- يجب أن يتطابق `API_BASE_URL` مع الدومين والمنفذ الذي يعمل عليه الـ API حتى يُبنى الـ Redirect URI بشكل صحيح.

### 2. التحقق من الإعدادات في الكود

في `crm_saas_api/settings.py` يجب أن تظهر (وعادةً موجودة):

```python
TIKTOK_CLIENT_ID = os.getenv("TIKTOK_CLIENT_ID", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REDIRECT_URI = f"{API_BASE_URL}/api/integrations/accounts/oauth/callback/tiktok/"
```

لا تحتاج لتعديلها إن كنت تستخدم أسماء المتغيرات نفسها في `.env`.

### 3. الجلسات (Session) لـ OAuth

تدفق الربط يعتمد على **Session** لحفظ `state` و`code_verifier` (PKCE) بين طلب «Connect» وطلب «Callback».

- تأكد أن **Session middleware** مفعّل في `settings.py` (عادةً مفعّل افتراضياً).
- في الإنتاج، إن كان الـ Frontend والـ API على دومينات مختلفة، راجع إعدادات **Cookie** (مثل `SESSION_COOKIE_SAMESITE`, `CSRF_COOKIE_DOMAIN`) حتى تُرسل الـ Session عند استدعاء callback من المتصفح إلى نفس دومين الـ API.

### 4. تشغيل الـ Backend

```bash
cd CRM-api-1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

تأكد أن الـ API متاح على نفس الـ `API_BASE_URL` (مثلاً `http://localhost:8000`).

---

## 🔧 إعداد الـ Frontend (CRM-project)

### 1. متغيرات البيئة

في مجلد `CRM-project`:

```env
VITE_API_URL=http://localhost:8000
# أو الإنتاج: VITE_API_URL=https://api.yourdomain.com
```

يُستخدم لجميع استدعاءات الـ API (بما فيها التكاملات).

### 2. الوصول لصفحة TikTok في التكاملات

- في التطبيق، اذهب إلى **Integrations** ثم اختر **TikTok** (أو المسار المعتمد في الـ routing، مثل `/integrations/tiktok`).
- الصفحة `IntegrationsPage.tsx` تعرض حسابات TikTok وتستدعي `getConnectedAccountsAPI('tiktok')`.

لا تحتاج لتعديل الكود إن كان الـ routing يوجّه إلى نفس الصفحة مع `currentPage === 'TikTok'`.

---

## 🔄 تدفق OAuth من البداية للنهاية

### 1. إضافة «حساب تكامل» جديد (TikTok)

- المستخدم يفتح **Integrations → TikTok** ويضغط **Add new account** (أو ما يعادله).
- في النافذة يختار المنصة **TikTok** ويُدخل اسماً للحساب (مثل: "حساب TikTok الرسمي") ويحفظ.
- الـ Frontend يستدعي `POST /api/integrations/accounts/` بجسم مثل:  
  `{ "platform": "tiktok", "name": "..." }`.  
  يُنشأ سجل `IntegrationAccount` بحالة غير مرتبط.

### 2. بدء الربط (Connect)

- المستخدم يضغط **Connect** بجانب الحساب.
- الـ Frontend يستدعي:  
  `POST /api/integrations/accounts/{id}/connect/`
- الـ Backend:
  - يولد `state` عشوائي.
  - لـ TikTok يولد أيضاً `code_verifier` ثم `code_challenge` (PKCE).
  - يخزن في الـ Session: `oauth_state_{account_id}`, `oauth_account_id_{state}`, ولـ TikTok أيضاً `oauth_code_verifier_{account_id}`.
  - يبني رابط التفويض:
    - `https://www.tiktok.com/v2/auth/authorize`
    - مع: `client_key`, `scope`, `response_type=code`, `redirect_uri`, `state`, `code_challenge`, `code_challenge_method=S256`.
  - يعيد `{ "authorization_url": "...", "state": "..." }`.
- الـ Frontend يوجّه المستخدم إلى `authorization_url` (نفس النافذة أو نافذة جديدة).

### 3. المستخدم على TikTok

- يفتح TikTok ويُسجّل الدخول إن لم يكن مسجلاً.
- يوافق على الصلاحيات (Scopes) المطلوبة.
- TikTok يوجّه المتصفح إلى الـ **Redirect URI** مع:
  - `code=...`
  - `state=...`
  - (أو `error` و`error_description` في حال الرفض أو الخطأ).

### 4. معالجة الـ Callback في الـ Backend

- الطلب يصل إلى:  
  `GET /api/integrations/accounts/oauth/callback/tiktok/?code=...&state=...`
- الـ Backend:
  - يتحقق من `state` (يقرأ `oauth_account_id_{state}` من الـ Session).
  - يقرأ `code_verifier` من الـ Session لنفس الحساب.
  - يستدعي TikTok:  
    `POST https://open.tiktokapis.com/v2/oauth/token/`  
    بجسم `application/x-www-form-urlencoded`:  
    `client_key`, `client_secret`, `code`, `grant_type=authorization_code`, `redirect_uri`, `code_verifier`.
  - يحصل على `access_token`, `refresh_token`, `expires_in`.
  - (اختياري) يستدعي TikTok لـ User Info ويحفظ الاسم والمعرف في `IntegrationAccount`.
  - يحدّث الحساب: يخزن التوكنات (مشفّرة)، يضع الحالة `connected`.
  - ينظّف الـ Session من `state` و`code_verifier`.
  - يوجّه المستخدم إلى الواجهة:  
    `{FRONTEND_URL}/integrations?connected=true&account_id={id}`  
    (أو مسار TikTok إن كان مختلفاً في تطبيقك).

### 5. بعد العودة للواجهة

- الصفحة تقرأ `connected=true` و`account_id` من الـ URL وتحدّث القائمة (مثلاً بإعادة جلب قائمة الحسابات).
- يظهر الحساب بحالة **Connected**.

### 6. تجديد التوكن (Sync / خلفية)

- Access token من TikTok صالح لمدة 24 ساعة تقريباً.
- عند استدعاء **Sync** أو أي منطق يستخدم التوكن، الـ Backend يتحقق من انتهاء الصلاحية ويستدعي:
  - `POST https://open.tiktokapis.com/v2/oauth/token/`  
  - `grant_type=refresh_token`, `refresh_token=...`, `client_key`, `client_secret`.
- يُحدّث التوكن المخزّن ويواصل العمل.

---

## 🧪 الاختبار

### 1. التحقق من الإعداد

- تأكد أن `TIKTOK_CLIENT_ID` و`TIKTOK_CLIENT_SECRET` و`API_BASE_URL` و`INTEGRATION_ENCRYPTION_KEY` و`FRONTEND_URL` مضبوطة في `.env`.
- تأكد أن الـ Redirect URI المسجّل في TikTok يطابق بالضبط:  
  `{API_BASE_URL}/api/integrations/accounts/oauth/callback/tiktok/`

### 2. اختبار الربط

1. شغّل الـ Backend والـ Frontend.
2. سجّل الدخول للـ CRM.
3. اذهب إلى **Integrations → TikTok**.
4. اضغط **Add new account**، اختر TikTok وأعطِ اسماً، واحفظ.
5. اضغط **Connect** على الحساب الجديد.
6. يجب أن تُوجّه لصفحة TikTok للموافقة.
7. بعد الموافقة يجب أن تعود لصفحة التكاملات مع `?connected=true&account_id=...` وأن يظهر الحساب **Connected**.

### 3. أخطاء شائعة

- **Redirect URI mismatch:**  
  تحقق أن الـ URI في TikTok = `API_BASE_URL` + `/api/integrations/accounts/oauth/callback/tiktok/` (بدون أي اختلاف في الـ path أو الـ trailing slash إن كان الـ Backend يضيفه).
- **Invalid state / Code verifier not found:**  
  الجلسة لم تُحفظ أو انتهت. تأكد أن الطلب إلى `/connect/` والطلب إلى `/oauth/callback/tiktok/` يأتيان من نفس المتصفح ونفس الدومين (والكوكيز مفعّلة).
- **401 على الـ callback:**  
  الـ callback يجب أن يُستدعى في سياق مستخدم مسجّل دخوله (Session). إن كان الـ Frontend على دومين والـ API على دومين آخر، قد لا تُرسل كوكي الجلسة؛ راجع إعدادات الـ Cookie والـ CORS.

---

## 🚀 النشر (Production)

1. **HTTPS:**  
   استخدم دومين `https` للـ API وثبّت هذا الـ URL في TikTok كـ Redirect URI.

2. **متغيرات البيئة:**  
   اضبط في السيرفر:
   - `API_BASE_URL=https://api.yourdomain.com`
   - `FRONTEND_URL=https://app.yourdomain.com`
   - `TIKTOK_CLIENT_ID` و`TIKTOK_CLIENT_SECRET` (قيم Production من TikTok).
   - `INTEGRATION_ENCRYPTION_KEY` (مفتاح قوي ومُخزّن بأمان).

3. **تطبيق TikTok:**  
   قد تحتاج لتمرير **App Review** في TikTok للاستخدام الكامل في Production.

4. **الجلسات:**  
   استخدم تخزين جلسات آمن (مثل Redis أو قاعدة البيانات) وإعدادات Cookie مناسبة حتى تبقى `state` و`code_verifier` متاحة عند الـ callback.

---

## 🐛 استكشاف الأخطاء

| العرض / الخطأ | السبب المحتمل | الحل |
|---------------|----------------|------|
| Redirect_uri is not matched | الـ URI المسجّل في TikTok لا يطابق الطلب | تطابق تام مع `TIKTOK_REDIRECT_URI` (من `API_BASE_URL`). |
| Invalid state | الجلسة انتهت أو لم تُحفظ | نفس الدومين للـ API، تفعيل الجلسات والكوكيز. |
| Code verifier not found | الجلسة لا تحتوي `oauth_code_verifier_{account_id}` | التأكد أن الطلب لـ `/connect/` تم من نفس المتصفح قبل التوجيه لـ TikTok. |
| scope not authorized | الـ Scope غير مفعّل في التطبيق | تفعيل الـ Scopes في لوحة TikTok (Products → Login Kit → Scopes). |
| 403 من الـ API | اشتراك الشركة غير نشط أو صلاحيات | التحقق من `HasActiveSubscription` وكون المستخدم تابعاً للشركة الصحيحة. |

---

## 📚 المراجع الرسمية

- **TikTok for Developers:** https://developers.tiktok.com/
- **إنشاء تطبيق:** https://developers.tiktok.com/doc/getting-started-create-an-app
- **Login Kit للويب:** https://developers.tiktok.com/doc/login-kit-web
- **إدارة توكنات المستخدم (OAuth v2):** https://developers.tiktok.com/doc/login-kit-manage-user-access-tokens
- **إرشادات مراجعة التطبيق:** https://developers.tiktok.com/doc/app-review-guidelines

---

تم إعداد هذا الدليل ليتوافق مع بنية مشروعك الحالية (CRM-api-1 و CRM-project) ومع وثائق TikTok المحدثة حتى 2026. إن تغيّرت عناوين أو أسماء حقول في TikTok، حدّث الروابط والقيم وفق الوثائق الرسمية.
