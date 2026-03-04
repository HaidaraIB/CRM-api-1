# إعداد ربط واتساب (WhatsApp Business API) في الـ CRM

## 1. خطأ "Invalid Scopes: whatsapp_business_management, whatsapp_business_messaging"

هذا يظهر عندما **تطبيق فيسبوك** لا يضم منتج واتساب أو الصلاحيات غير مضافة.

### ما الذي تفعله في [Meta for Developers](https://developers.facebook.com/):

1. افتح تطبيقك → **App Dashboard**.
2. من القائمة الجانبية: **Add Products** (إضافة منتجات).
3. ابحث عن **WhatsApp** وأضفه للتطبيق.
4. بعد إضافة واتساب، من **App Review** → **Permissions and Features** تأكد أن الطلبات التالية مضافة ومُفعّلة حسب الحاجة:
   - `whatsapp_business_management`
   - `whatsapp_business_messaging`
   - `business_management`
5. إذا كان التطبيق للاستخدام الداخلي فقط، يمكنك استخدامه في وضع **Development** مع إضافة نفسك كمختبر. للاستخدام العام قد تحتاج **App Review** و Advanced Access لهذه الصلاحيات.

---

## 2. خطأ "لا يمكن تحميل عنوان URL" / URL not in App Domains

هذا يظهر عندما **نطاق عنوان الـ callback** غير مضاف في إعدادات التطبيق.

### ما الذي تفعله:

1. من لوحة التطبيق: **Settings** → **Basic**.
2. في **App Domains** أضف **نطاق الـ API فقط** (بدون `https://` أو مسار):
   - إذا الـ API يعمل على: `https://api.loop-crm.app` ← أضف: `api.loop-crm.app`
   - إذا محلياً: `http://localhost:8000` ← أضف: `localhost`
3. من **Products** → **Facebook Login** (أو **Facebook Login for Business**) → **Settings**:
   - في **Valid OAuth Redirect URIs** أضف عنوان الـ callback **كامل** كما يظهر في الطلب، مثلاً:
     - إنتاج: `https://api.loop-crm.app/api/integrations/accounts/oauth/callback/whatsapp/`
     - محلي: `http://localhost:8000/api/integrations/accounts/oauth/callback/whatsapp/`
4. احفظ التغييرات وجرب "Connect" مرة أخرى.

---

## 3. التحقق من القيم في المشروع

تأكد في ملف `.env` أن:

- `API_BASE_URL` = نفس النطاق المستخدم في Redirect URI (مثلاً `https://api.loop-crm.app` أو `http://localhost:8000`).
- `WHATSAPP_CLIENT_ID` و `WHATSAPP_CLIENT_SECRET` (أو `META_CLIENT_ID` و `META_CLIENT_SECRET`) هما نفس قيم تطبيق Meta الذي أضفت له واتساب والنطاقات أعلاه.

بعد تطبيق الخطوات 1 و 2 و 3، أعد محاولة ربط واتساب من صفحة التكاملات.
