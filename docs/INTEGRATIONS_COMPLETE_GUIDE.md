# 📘 دليل التكاملات الكامل - Meta & WhatsApp & TikTok

> **دليل شامل واحد لجميع التكاملات** - Meta (Facebook/Instagram), WhatsApp, TikTok

**📌 تكامل TikTok (Lead Gen فقط – مثل Meta Lead Form):** [دليل TikTok Lead Gen](./TIKTOK_INTEGRATION_GUIDE.md) | [دليل المشترك (ماذا يفعل المشترك)](./TIKTOK_LEADGEN_SUBSCRIBER_GUIDE.md) | [TikTok for Business خطوة بخطوة](./TIKTOK_LEADGEN_TIKTOK_FOR_BUSINESS_GUIDE.md)

---

## 📑 جدول المحتويات

1. [⚡ البدء السريع (5 دقائق)](#-البدء-السريع-5-دقائق)
2. [📋 نظرة عامة](#-نظرة-عامة)
3. [🏗️ كيف يعمل النظام؟](#️-كيف-يعمل-النظام)
4. [🔧 إعداد Meta App](#-إعداد-meta-app)
5. [🔧 إعداد Backend](#-إعداد-backend)
6. [🔧 إعداد Frontend](#-إعداد-frontend)
7. [🧪 الاختبار](#-الاختبار)
8. [🚀 النشر على VPS](#-النشر-على-vps)
9. [🐛 استكشاف الأخطاء](#-استكشاف-الأخطاء)
10. [📊 Monitoring](#-monitoring)
11. [🔐 الأمان](#-الأمان)

---

## ⚡ البدء السريع (5 دقائق)

### 1. إعداد Meta App (مرة واحدة)

1. اذهب إلى: https://developers.facebook.com/
2. أنشئ App جديد → **Business**
3. في **Settings → Basic**:
   - انسخ `App ID` → هذا هو `META_CLIENT_ID`
   - انسخ `App Secret` → هذا هو `META_CLIENT_SECRET`
4. في **Facebook Login → Settings**:
   - أضف Redirect URI: `http://localhost:8000/api/integrations/accounts/oauth/callback/meta/`
   
   **⚠️ مهم:** تأكد من أن الـ URL صحيح تماماً!
5. في **Lead Ads → Settings**:
   - Webhook URL: `http://localhost:8000/api/integrations/webhooks/meta/`
   - Verify Token: أنشئ token (مثل: `test123`)
6. في **App Review → Permissions**:
   - أضف: `leads_retrieval`, `pages_read_engagement`

---

### 2. إعداد Backend

```bash
cd CRM-api-1

# إنشاء .env
cat > .env << EOF
META_CLIENT_ID=your_app_id_here
META_CLIENT_SECRET=your_app_secret_here
META_WEBHOOK_VERIFY_TOKEN=test123

# API Base URL (يستخدم لبناء Redirect URI تلقائياً)
API_BASE_URL=http://localhost:8000

# إنشاء Encryption Key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# انسخ الناتج وأضفه:
INTEGRATION_ENCRYPTION_KEY=paste_key_here

SECRET_KEY=your_secret_key_here
DEBUG=True
EOF

# تثبيت المتطلبات
pip install -r requirements.txt

# Migrations
python manage.py migrate

# تشغيل Server
python manage.py runserver
```

---

### 3. إعداد Frontend

```bash
cd CRM-project

# إنشاء .env
echo "REACT_APP_API_URL=http://localhost:8000/api" > .env

# تثبيت المتطلبات
npm install

# تشغيل
npm start
```

---

### 4. الاختبار

1. افتح: `http://localhost:3000`
2. اذهب إلى: **Integrations → Meta**
3. اضغط **Connect**
4. سجل دخول Meta ووافق على الصلاحيات
5. بعد العودة، اختر Lead Form و Campaign
6. أنشئ Lead Form Test على Facebook
7. املأ الفورم
8. تحقق من أن الليد ظهر في **LeadsPage** مع Source = "Meta"

---

## 📋 نظرة عامة

هذا النظام يربط CRM مع Meta (Facebook & Instagram) و WhatsApp لاستقبال الليدز تلقائياً. كل شركة مشتركة يمكنها ربط حسابها الخاص واستقبال الليدز في حسابها المعزول.

### الميزات:
- ✅ ربط حسابات Meta/WhatsApp لكل شركة بشكل منفصل
- ✅ استقبال الليدز تلقائياً من Meta Lead Forms
- ✅ استقبال رسائل WhatsApp تلقائياً
- ✅ ربط الليدز بالكامبينز
- ✅ Auto-assignment للليدز
- ✅ تشفير Tokens
- ✅ Background Tasks لتجديد Tokens

---

## 🏗️ كيف يعمل النظام؟

### 1. البنية المعمارية

```
┌─────────────────┐
│   Meta App      │  (واحد لجميع الشركات)
│  Client ID/Secret│
└────────┬────────┘
         │
         │ OAuth Flow
         ▼
┌─────────────────┐
│  Loop CRM API   │
│  (Django)       │
└────────┬────────┘
         │
         │ Store Access Token
         │ (Encrypted per Company)
         ▼
┌─────────────────┐
│ IntegrationAccount│ (لكل شركة)
│  - company_id   │
│  - access_token │
│  - page_id      │
│  - form_id      │
└────────┬────────┘
         │
         │ Webhook Events
         ▼
┌─────────────────┐
│  Meta Webhook │
│  → Create Lead  │
│  → Assign       │
└─────────────────┘
```

### 2. تدفق العمل

#### أ. ربط حساب Meta:
1. المستخدم يضغط "Connect" في صفحة Integrations
2. يتم توجيهه إلى Meta OAuth
3. Meta يرجع `authorization_code`
4. Backend يستبدل الكود بـ `access_token`
5. Backend يجلب Pages و Lead Forms
6. المستخدم يختار Lead Form ويربطه بكامبين
7. يتم حفظ `IntegrationAccount` مع `form_id` و `campaign_id`

#### ب. استقبال الليدز:
1. عميل يملأ Lead Form على Facebook/Instagram
2. Meta يرسل Webhook إلى `/webhooks/meta/`
3. Backend يتحقق من التوقيع
4. Backend يبحث عن `IntegrationAccount` باستخدام `form_id`
5. Backend يجلب بيانات الليد من Meta API
6. Backend ينشئ `Client` جديد
7. Backend يربط الليد بالـ `Campaign` (إذا كان محدد)
8. Backend يعين الليد تلقائياً (إذا كان Auto-assign مفعل)

---

## 🔧 إعداد Meta App

### الخطوة 1: إنشاء Meta App

1. اذهب إلى: https://developers.facebook.com/
2. سجل دخول بحساب Facebook الخاص بك
3. اضغط على **"My Apps"** في القائمة العلوية
4. اضغط على **"Create App"**
5. اختر نوع App: **"Business"** (لأنك تريد Lead Ads)
6. أدخل:
   - **App Name**: `Loop CRM` (أو أي اسم تريده)
   - **App Contact Email**: بريدك الإلكتروني
7. اضغط **"Create App"**

---

### الخطوة 2: الحصول على Client ID و Client Secret

1. في App Dashboard، اذهب إلى **Settings** → **Basic**
2. **App ID**: هذا هو `META_CLIENT_ID` - انسخه واحفظه
3. **App Secret**: 
   - اضغط على **"Show"** بجانب App Secret
   - قد يطلب منك كلمة مرور Facebook
   - هذا هو `META_CLIENT_SECRET` - انسخه واحفظه (⚠️ لا تشاركه أبداً!)

---

### الخطوة 3: إضافة Products

#### أ. Facebook Login:
1. في App Dashboard، اضغط **"Add Product"**
2. ابحث عن **"Facebook Login"** واختره
3. اضغط **"Set Up"**
4. اختر **Web** كمنصة

#### ب. Lead Ads:
1. في App Dashboard، اضغط **"Add Product"**
2. ابحث عن **"Lead Ads"** أو **"Lead Generation"**
3. اضغط **"Set Up"**

---

### الخطوة 4: إعداد OAuth Redirect URI

**⚠️ هذا مهم جداً!**

1. في App Dashboard، اذهب إلى **Products** → **Facebook Login** → **Settings**
2. في قسم **"Valid OAuth Redirect URIs"**، أضف:

#### للاختبار المحلي:
```
http://localhost:8000/api/integrations/accounts/oauth/callback/meta/
```

#### للإنتاج:
```
https://yourdomain.com/api/integrations/accounts/oauth/callback/meta/
```

**⚠️ ملاحظات مهمة:**
- يجب أن ينتهي بـ `/meta/` (وليس فقط `/`)
- يجب أن يكون المسار كاملاً: `/api/integrations/accounts/oauth/callback/meta/`
- يجب أن يكون `https://` في الإنتاج (وليس `http://`)

---

### الخطوة 5: إعداد Webhook

#### أ. إنشاء Webhook Verify Token:
أنشئ token عشوائي قوي:

**الطريقة 1: استخدام Python**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**الطريقة 2: استخدام OpenSSL**
```bash
openssl rand -hex 32
```

**مثال**: `aB3xY9mN2pQ7rT5vW8zC1dF4gH6jK0lM`

#### ب. إضافة Webhook في Meta App:
1. في App Dashboard، اذهب إلى **Products** → **Lead Ads** → **Webhooks**
2. أو اذهب إلى **Settings** → **Webhooks**
3. اضغط **"Add Callback URL"** أو **"Create Webhook"**
4. أدخل:
   - **Callback URL**: 
     ```
     https://yourdomain.com/api/integrations/webhooks/meta/
     ```
     - للاختبار المحلي، استخدم ngrok (انظر قسم الاختبار)
   
   - **Verify Token**: 
     ```
     نفس القيمة التي أنشأتها في الخطوة أ
     ```
     - مثال: `aB3xY9mN2pQ7rT5vW8zC1dF4gH6jK0lM`
5. اضغط **"Verify and Save"**

#### ج. إضافة Subscription:
1. بعد إضافة Webhook، اضغط على **"Edit"** بجانب Webhook
2. في **Subscription Fields**، أضف:
   - `leadgen` ⭐ (مهم جداً!)

---

### الخطوة 6: إضافة Permissions

في **App Review** → **Permissions and Features**، أضف:

- `pages_show_list` - لعرض قائمة الصفحات
- `pages_read_engagement` - لقراءة التفاعلات
- `pages_manage_metadata` - لإدارة البيانات الوصفية
- `pages_manage_posts` - لإدارة المنشورات (اختياري)
- `leads_retrieval` - لجلب الليدز ⭐ (مهم جداً!)
- `business_management` - لإدارة الأعمال
- `ads_management` - لإدارة الإعلانات (اختياري)

**ملاحظة**: بعض الصلاحيات قد تحتاج موافقة من Meta (App Review). للاختبار، يمكنك استخدام **Development Mode**.

---

### الخطوة 7: إعداد Website Platform

1. في **Settings** → **Basic** → **Add Platform**
2. اختر **Website**
3. أضف **Site URL**: `https://yourdomain.com`
4. للاختبار المحلي: `http://localhost:8000`

---

## 🔧 إعداد Backend

### 1. إنشاء ملف `.env`

```bash
cd CRM-api-1
cp .env.example .env  # إذا كان موجود
```

أضف هذه المتغيرات:

```env
# ==================== Meta Integration ====================
META_CLIENT_ID=your_meta_app_id
META_CLIENT_SECRET=your_meta_app_secret
META_WEBHOOK_VERIFY_TOKEN=your_secure_verify_token

# ==================== API Base URL ====================
# يستخدم لبناء Redirect URI تلقائياً
API_BASE_URL=https://yourdomain.com
# أو للاختبار المحلي:
# API_BASE_URL=http://localhost:8000

# ==================== Encryption ====================
# مهم جداً! يجب أن يكون 32 حرف base64
INTEGRATION_ENCRYPTION_KEY=your_32_character_base64_key

# ==================== Database ====================
DATABASE_URL=postgresql://user:password@localhost:5432/crm_db
# أو للاختبار:
# DATABASE_URL=sqlite:///db.sqlite3

# ==================== Django ====================
SECRET_KEY=your_django_secret_key
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

# ==================== CORS ====================
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# ==================== Frontend ====================
FRONTEND_URL=https://app.yourdomain.com
```

---

### 2. إنشاء Encryption Key

```python
from cryptography.fernet import Fernet
key = Fernet.generate_key()
print(key.decode())  # انسخ هذا في INTEGRATION_ENCRYPTION_KEY
```

أو من Terminal:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

### 3. تثبيت المتطلبات

```bash
pip install -r requirements.txt
```

---

### 4. تشغيل Migrations

```bash
python manage.py migrate
```

---

### 5. إنشاء Superuser

```bash
python manage.py createsuperuser
```

---

## 🔧 إعداد Frontend

### 1. إنشاء ملف `.env`

```bash
cd CRM-project
```

أنشئ `.env`:
```env
REACT_APP_API_URL=https://yourdomain.com/api
# أو للاختبار المحلي:
# REACT_APP_API_URL=http://localhost:8000/api
```

---

### 2. تثبيت المتطلبات

```bash
npm install
```

---

## 🧪 الاختبار

### 1. اختبار Backend محلياً

#### أ. تشغيل Server:
```bash
cd CRM-api-1
python manage.py runserver
```

#### ب. اختبار OAuth Flow:
1. افتح: `http://localhost:3000/integrations/meta`
2. اضغط **Connect**
3. يجب أن يتم توجيهك إلى Meta Login
4. بعد الموافقة، يجب أن تعود إلى Callback URL
5. تحقق من Database:
   ```bash
   python manage.py shell
   ```
   ```python
   from integrations.models import IntegrationAccount
   IntegrationAccount.objects.filter(platform='meta')
   ```

#### ج. اختبار Webhook (محلياً):
استخدم [ngrok](https://ngrok.com/) لتعريض localhost:

```bash
# تثبيت ngrok
# https://ngrok.com/download

# تشغيل ngrok
ngrok http 8000
```

ثم:
1. انسخ الـ URL (مثل: `https://abc123.ngrok.io`)
2. في Meta App → Lead Ads → Webhook URL:
   - `https://abc123.ngrok.io/api/integrations/webhooks/meta/`
3. انقر "Verify and Save"
4. أنشئ Lead Form Test على Facebook
5. املأ الفورم
6. تحقق من Logs:
   ```bash
   python manage.py runserver
   # ستظهر جميع الطلبات في Console
   ```

#### د. اختبار API Endpoints:
```bash
# جلب Lead Forms
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/api/integrations/accounts/1/lead_forms/?page_id=PAGE_ID

# ربط Lead Form
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"page_id": "PAGE_ID", "form_id": "FORM_ID", "campaign_id": 1}' \
  http://localhost:8000/api/integrations/accounts/1/select_lead_form/
```

---

### 2. اختبار Frontend محلياً

#### أ. تشغيل Development Server:
```bash
cd CRM-project
npm start
```

#### ب. اختبار التكامل:
1. افتح: `http://localhost:3000`
2. اذهب إلى: **Integrations → Meta**
3. اضغط **Connect**
4. بعد OAuth، يجب أن يظهر Modal لاختيار Lead Form
5. اختر Lead Form و Campaign
6. تحقق من أن الليدز تظهر في LeadsPage مع Source = "Meta"

---

### 3. التحقق من Redirect URI

يمكنك التحقق من الـ URL الذي يستخدمه الكود:

```bash
cd CRM-api-1
python manage.py shell
```

```python
from django.conf import settings
print(settings.META_REDIRECT_URI)
```

يجب أن يطابق الـ URL في Meta App تماماً!

---

## 🚀 النشر على VPS

### 1. إعداد VPS

#### أ. تحديث النظام:
```bash
sudo apt update && sudo apt upgrade -y
```

#### ب. تثبيت المتطلبات:
```bash
# Python & pip
sudo apt install python3.9 python3.9-venv python3-pip -y

# PostgreSQL
sudo apt install postgresql postgresql-contrib -y

# Nginx
sudo apt install nginx -y

# Supervisor (لإدارة Processes)
sudo apt install supervisor -y

# Redis (اختياري - للـ Background Tasks)
sudo apt install redis-server -y
```

#### ج. إعداد PostgreSQL:
```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE crm_db;
CREATE USER crm_user WITH PASSWORD 'your_secure_password';
ALTER ROLE crm_user SET client_encoding TO 'utf8';
ALTER ROLE crm_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE crm_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE crm_db TO crm_user;
\q
```

---

### 2. نشر Backend

#### أ. رفع الملفات:
```bash
# على جهازك المحلي
cd CRM-api-1
scp -r . user@your_vps_ip:/var/www/crm-api/
```

أو استخدم Git:
```bash
# على VPS
cd /var/www
git clone your_repo_url crm-api
cd crm-api
```

#### ب. إعداد Virtual Environment:
```bash
cd /var/www/crm-api
python3.9 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### ج. إعداد `.env`:
```bash
nano .env
# أضف جميع المتغيرات كما في الإعداد الأولي
```

#### د. تشغيل Migrations:
```bash
source venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
```

#### هـ. إنشاء Supervisor Config:
```bash
sudo nano /etc/supervisor/conf.d/crm-api.conf
```

أضف:
```ini
[program:crm-api]
command=/var/www/crm-api/venv/bin/gunicorn crm_saas_api.wsgi:application --bind 127.0.0.1:8000 --workers 3
directory=/var/www/crm-api
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/crm-api.log
```

#### و. تشغيل Supervisor:
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start crm-api
```

#### ز. إعداد Nginx:
```bash
sudo nano /etc/nginx/sites-available/crm-api
```

أضف:
```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /var/www/crm-api/staticfiles/;
    }

    location /media/ {
        alias /var/www/crm-api/media/;
    }
}
```

تفعيل:
```bash
sudo ln -s /etc/nginx/sites-available/crm-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### ح. إعداد SSL (Let's Encrypt):
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

---

### 3. نشر Frontend

#### أ. Build Production:
```bash
# على جهازك المحلي
cd CRM-project
npm run build
```

#### ب. رفع Build:
```bash
scp -r build/* user@your_vps_ip:/var/www/crm-frontend/
```

#### ج. إعداد Nginx للـ Frontend:
```bash
sudo nano /etc/nginx/sites-available/crm-frontend
```

أضف:
```nginx
server {
    listen 80;
    server_name app.yourdomain.com;

    root /var/www/crm-frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

تفعيل:
```bash
sudo ln -s /etc/nginx/sites-available/crm-frontend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

### 4. إعداد Background Tasks

إذا كنت تستخدم Django Q2 أو Celery:

#### أ. Supervisor Config:
```bash
sudo nano /etc/supervisor/conf.d/crm-worker.conf
```

```ini
[program:crm-worker]
command=/var/www/crm-api/venv/bin/python manage.py qcluster
directory=/var/www/crm-api
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/crm-worker.log
```

#### ب. تشغيل:
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start crm-worker
```

---

## 🐛 استكشاف الأخطاء

### 1. Redirect URI Mismatch

**الخطأ:**
```
Redirect URI mismatch
```

**الحل:**
1. تحقق من أن الـ URL في Meta App يطابق تماماً الـ URL في `.env`
2. تأكد من وجود `/meta/` في النهاية
3. تأكد من `http://` للاختبار المحلي و `https://` للإنتاج
4. احفظ التغييرات في Meta App وانتظر دقيقة
5. أعد تشغيل Django Server بعد تغيير `.env`

**التحقق:**
```bash
python manage.py shell
from django.conf import settings
print(settings.META_REDIRECT_URI)
```

---

### 2. Webhook Verification Failed

**الخطأ:**
```
Webhook verification failed
```

**الحل:**
- ✅ تأكد من تطابق `META_WEBHOOK_VERIFY_TOKEN`
- ✅ تأكد من أن URL صحيح
- ✅ تأكد من أن Server يعمل
- ✅ تحقق من Logs:
  ```bash
  tail -f /var/log/crm-api.log
  ```

---

### 3. OAuth Failed

**الخطأ:**
```
Invalid client_id or client_secret
```

**الحل:**
- ✅ تأكد من `META_CLIENT_ID` و `META_CLIENT_SECRET` صحيحين
- ✅ تأكد من إضافة Redirect URI في Meta App
- ✅ تأكد من الصلاحيات المطلوبة
- ✅ تحقق من CORS Settings

---

### 4. Tokens منتهية

**الخطأ:**
```
Token expired
```

**الحل:**
- تحقق من Background Task (يجب أن يجدد Tokens تلقائياً)
- يمكنك تجديد يدوياً:
  ```python
  python manage.py shell
  from integrations.tasks import refresh_expired_tokens
  refresh_expired_tokens()
  ```

---

### 5. الليدز لا تظهر

**الخطأ:**
```
Leads not appearing in CRM
```

**الحل:**
- ✅ تحقق من Webhook Logs
- ✅ تحقق من أن `form_id` موجود في `IntegrationAccount`
- ✅ تحقق من أن Lead Form مربوط بكامبين
- ✅ تحقق من Database:
  ```sql
  SELECT * FROM crm_client WHERE source = 'meta_lead_form';
  ```
- ✅ تحقق من `IntegrationLog` للأخطاء:
  ```python
  from integrations.models import IntegrationLog
  IntegrationLog.objects.filter(status='error').order_by('-created_at')[:10]
  ```

---

### 6. Invalid redirect_uri

**الحل:**
- تأكد من أن `API_BASE_URL` في `.env` صحيح
- أعد تشغيل Django Server بعد تغيير `.env`

---

### 7. الـ URL لا يعمل

**الحل:**
- تحقق من أن Django Server يعمل
- تحقق من أن الـ URL موجود في `urls.py`
- تحقق من Logs:
  ```bash
  python manage.py runserver
  # ستظهر جميع الطلبات في Console
  ```

---

## 📊 Monitoring

### 1. Logs:
```bash
# Backend Logs
tail -f /var/log/crm-api.log

# Supervisor Logs
sudo supervisorctl tail -f crm-api

# Nginx Logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

### 2. Database Queries:
```bash
# عدد الليدز من Meta
psql -U crm_user -d crm_db -c "SELECT COUNT(*) FROM crm_client WHERE source = 'meta_lead_form';"

# Integration Accounts
psql -U crm_user -d crm_db -c "SELECT id, name, platform, status FROM integrations_integrationaccount;"

# Integration Logs
psql -U crm_user -d crm_db -c "SELECT * FROM integrations_integrationlog ORDER BY created_at DESC LIMIT 10;"
```

### 3. Python Shell:
```bash
python manage.py shell
```

```python
# عدد Integration Accounts
from integrations.models import IntegrationAccount
IntegrationAccount.objects.count()

# عدد الليدز من Meta
from crm.models import Client
Client.objects.filter(source='meta_lead_form').count()

# آخر الأخطاء
from integrations.models import IntegrationLog
IntegrationLog.objects.filter(status='error').order_by('-created_at')[:10]
```

---

## 🔐 الأمان

### 1. Environment Variables:
- لا ترفع `.env` إلى Git
- استخدم قيم قوية لـ `SECRET_KEY` و `INTEGRATION_ENCRYPTION_KEY`
- راجع `.env` بانتظام

### 2. SSL:
- استخدم HTTPS دائماً
- تجدد شهادات SSL تلقائياً:
  ```bash
  sudo certbot renew --dry-run
  ```

### 3. Firewall:
```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 4. Encryption:
- جميع Tokens مشفرة في Database
- Encryption Key يجب أن يكون قوياً (32 حرف base64)
- لا تشارك Encryption Key أبداً

---

## 📝 Checklist قبل الإطلاق

- [ ] Meta App تم إعداده بالكامل
- [ ] جميع Environment Variables محددة
- [ ] Encryption Key تم إنشاؤه
- [ ] Database تم إعداده
- [ ] Migrations تم تشغيلها
- [ ] SSL Certificate مثبت
- [ ] Webhook URL تم التحقق منه في Meta
- [ ] OAuth Redirect URI صحيح
- [ ] Background Tasks تعمل
- [ ] Logs يتم تسجيلها
- [ ] Firewall مفعل
- [ ] Backup Strategy جاهزة
- [ ] تم اختبار OAuth Flow
- [ ] تم اختبار Webhook
- [ ] تم اختبار Lead Form

---

## 🔗 URLs المهمة

### OAuth Redirect URIs:
- **Meta**: `{API_BASE_URL}/api/integrations/accounts/oauth/callback/meta/`
- **WhatsApp**: `{API_BASE_URL}/api/integrations/accounts/oauth/callback/whatsapp/`
- **TikTok:** لا يوجد OAuth — التكامل = Lead Gen فقط (ويب هوك أدناه).

### Webhook URLs:
- **Meta**: `{API_BASE_URL}/api/integrations/webhooks/meta/`
- **WhatsApp**: `{API_BASE_URL}/api/integrations/webhooks/whatsapp/`
- **TikTok Lead Gen**: `{API_BASE_URL}/api/v1/integrations/webhooks/tiktok-leadgen/`

---

## 📚 روابط مفيدة

- Meta Developers: https://developers.facebook.com/
- Lead Ads Documentation: https://developers.facebook.com/docs/marketing-api/leadgen
- Webhook Testing: https://developers.facebook.com/tools/lead-ads-testing/
- ngrok: https://ngrok.com/

---

## 🆘 الدعم

إذا واجهت مشاكل:
1. تحقق من Logs
2. راجع هذا الدليل
3. تحقق من Meta App Settings
4. راجع قسم استكشاف الأخطاء

---

**آخر تحديث:** 2024

