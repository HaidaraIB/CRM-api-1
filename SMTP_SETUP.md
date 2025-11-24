# إعداد SMTP (SMTP Setup Guide)

## نظرة عامة
تم إعداد نظام SMTP لإرسال البث (Broadcasts) عبر البريد الإلكتروني. يمكنك إعداد SMTP من خلال واجهة API أو Django Admin.

## الخطوات

### 1. إنشاء Migration
قم بتشغيل الأمر التالي لإنشاء migration:
```bash
python manage.py makemigrations settings
python manage.py migrate
```

### 2. إعداد SMTP عبر API

#### الحصول على إعدادات SMTP الحالية:
```http
GET /api/settings/smtp/
Authorization: Bearer <your_token>
```

#### إنشاء/تحديث إعدادات SMTP:
```http
POST /api/settings/smtp/
PUT /api/settings/smtp/1/
PATCH /api/settings/smtp/1/

Content-Type: application/json
Authorization: Bearer <your_token>

{
  "host": "smtp.gmail.com",
  "port": 587,
  "use_tls": true,
  "use_ssl": false,
  "username": "your-email@gmail.com",
  "password": "your-app-password",
  "from_email": "your-email@gmail.com",
  "from_name": "Your Company Name",
  "is_active": true
}
```

#### اختبار الاتصال:
```http
POST /api/settings/smtp/test_connection/
Authorization: Bearer <your_token>
```

### 3. إعداد SMTP عبر Django Admin
1. افتح Django Admin
2. اذهب إلى Settings > SMTP Settings
3. أضف أو عدّل الإعدادات
4. احفظ التغييرات

## الحقول المطلوبة

- **host**: عنوان خادم SMTP (مثال: smtp.gmail.com)
- **port**: منفذ SMTP (587 للتشفير TLS، 465 للتشفير SSL، 25 بدون تشفير)
- **use_tls**: استخدام TLS (افتراضي: true)
- **use_ssl**: استخدام SSL (افتراضي: false)
- **username**: اسم المستخدم (عادةً عنوان البريد الإلكتروني)
- **password**: كلمة مرور SMTP (اتركه فارغًا للاحتفاظ بالقيمة الحالية)
- **from_email**: عنوان البريد الإلكتروني المرسل
- **from_name**: اسم المرسل (اختياري)
- **is_active**: تفعيل/تعطيل SMTP

## أمثلة لإعدادات مزودي البريد الشائعين

### Gmail
```json
{
  "host": "smtp.gmail.com",
  "port": 587,
  "use_tls": true,
  "use_ssl": false,
  "username": "your-email@gmail.com",
  "password": "your-app-password",
  "from_email": "your-email@gmail.com",
  "from_name": "Your Company",
  "is_active": true
}
```
**ملاحظة**: تحتاج إلى إنشاء App Password من Google Account Settings

### Outlook/Hotmail
```json
{
  "host": "smtp-mail.outlook.com",
  "port": 587,
  "use_tls": true,
  "use_ssl": false,
  "username": "your-email@outlook.com",
  "password": "your-password",
  "from_email": "your-email@outlook.com",
  "from_name": "Your Company",
  "is_active": true
}
```

### SendGrid
```json
{
  "host": "smtp.sendgrid.net",
  "port": 587,
  "use_tls": true,
  "use_ssl": false,
  "username": "apikey",
  "password": "your-sendgrid-api-key",
  "from_email": "your-verified-email@domain.com",
  "from_name": "Your Company",
  "is_active": true
}
```

### Mailgun
```json
{
  "host": "smtp.mailgun.org",
  "port": 587,
  "use_tls": true,
  "use_ssl": false,
  "username": "your-mailgun-username",
  "password": "your-mailgun-password",
  "from_email": "your-verified-email@domain.com",
  "from_name": "Your Company",
  "is_active": true
}
```

## الأمان

- **كلمة المرور**: عند تحديث الإعدادات، إذا تركت حقل `password` فارغًا، سيتم الاحتفاظ بكلمة المرور الحالية
- **التشفير**: يُنصح باستخدام TLS (port 587) أو SSL (port 465) بدلاً من الاتصال غير المشفر (port 25)
- **App Passwords**: لخدمات مثل Gmail، استخدم App Passwords بدلاً من كلمة المرور العادية

## استخدام البث (Broadcasts)

بعد إعداد SMTP وتفعيله (`is_active: true`)، يمكنك إرسال البث عبر:

```http
POST /api/broadcasts/{id}/send/
Authorization: Bearer <your_token>
```

سيتم إرسال البريد الإلكتروني تلقائيًا إلى:
- **all**: جميع الشركات مع اشتراكات نشطة
- **gold**: الشركات مع خطط Gold
- **trial**: الشركات مع اشتراكات تجريبية
- **expired**: الشركات مع اشتراكات منتهية

## قوالب البريد الإلكتروني

النظام يستخدم قوالب HTML احترافية للبث (Broadcasts) مع:
- تصميم متجاوب يعمل على جميع الأجهزة
- دعم اللغة العربية والإنجليزية
- ألوان متناسقة مع تصميم التطبيق
- تنسيق احترافي مع Header و Footer

القوالب موجودة في:
- `subscriptions/templates/subscriptions/broadcast_email.html` (العربية)
- `subscriptions/templates/subscriptions/broadcast_email_en.html` (الإنجليزية)

يمكنك تخصيص القوالب حسب احتياجاتك.

## استكشاف الأخطاء

### خطأ: "SMTP is not active"
- تأكد من تفعيل `is_active: true` في إعدادات SMTP

### خطأ: "Authentication failed"
- تحقق من صحة `username` و `password`
- لـ Gmail، تأكد من استخدام App Password

### خطأ: "Connection refused"
- تحقق من صحة `host` و `port`
- تأكد من أن جدار الحماية يسمح بالاتصال

### خطأ: "No recipients found"
- تأكد من وجود شركات/مستخدمين يطابقون الهدف المحدد
- تحقق من أن المستخدمين لديهم عناوين بريد إلكتروني صحيحة

