# دليل نشر CRM-api-1 على VPS للإنتاج

هذا الدليل يشرح كيفية نشر مشروع Django API (CRM-api-1) على خادم VPS في بيئة الإنتاج.

## المتطلبات الأساسية

### 1. متطلبات الخادم (VPS)
- نظام تشغيل: Ubuntu 20.04+ أو Debian 11+
- ذاكرة RAM: 2GB على الأقل (4GB موصى به)
- مساحة تخزين: 20GB على الأقل
- Python: الإصدار 3.10+ أو 3.11+ (موصى به)
- Nginx: كخادم ويب عكسي (Reverse Proxy)
- SSL Certificate: للحصول على HTTPS (Let's Encrypt)

### 2. متطلبات المشروع
- Python 3.10+ أو 3.11+
- pip
- virtualenv (اختياري لكن موصى به)
- Git

## خطوات النشر

### الخطوة 1: إعداد الخادم

#### 1.1 تحديث النظام
```bash
sudo apt update && sudo apt upgrade -y
```

#### 1.2 تثبيت Python و pip
```bash
# تثبيت Python 3.11
sudo apt install python3.11 python3.11-venv python3-pip -y

# التحقق من التثبيت
python3 --version
pip3 --version
```

#### 1.3 تثبيت Nginx (إذا لم يكن مثبتاً)
```bash
sudo apt install nginx -y
sudo systemctl start nginx
sudo systemctl enable nginx
```

#### 1.4 تثبيت Gunicorn
```bash
# سيتم تثبيته لاحقاً في virtualenv، لكن يمكن تثبيته عالمياً للتحقق
pip3 install gunicorn
```

### الخطوة 2: رفع المشروع إلى الخادم

#### 2.1 إنشاء مجلد المشروع
```bash
sudo mkdir -p /var/www/crm-api
sudo chown -R $USER:$USER /var/www/crm-api
```

#### 2.2 رفع الملفات
```bash
# من جهازك المحلي
scp -r CRM-api-1/* user@your-server-ip:/var/www/crm-api/
```

أو استنساخ من Git:
```bash
cd /var/www
sudo git clone <repository-url> crm-api
sudo chown -R $USER:$USER /var/www/crm-api
cd crm-api
```

### الخطوة 3: إعداد البيئة الافتراضية (Virtual Environment)

#### 3.1 إنشاء virtual environment
```bash
cd /var/www/crm-api
python3 -m venv venv
source venv/bin/activate
```

#### 3.2 تثبيت التبعيات
```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn  # إضافة Gunicorn للتبعيات
```

### الخطوة 4: إعداد متغيرات البيئة

#### 4.1 إنشاء ملف `.env`
```bash
cd /var/www/crm-api
nano .env
```

أضف المتغيرات التالية:
```env
# Django Settings
SECRET_KEY=your-secret-key-here-generate-a-strong-one
DEBUG=False
BASE_DOMAIN=loop-crm.app

# Database (SQLite - يمكن ترقيته لاحقاً)
# DATABASE_URL=sqlite:///db.sqlite3

# API Settings
API_BASE_URL=https://api.loop-crm.app
FRONTEND_URL=https://loop-crm.app
FRONTEND_APP_URL=https://loop-crm.app

# CORS Settings
CORS_ALLOW_ALL_ORIGINS=False

# PayTabs Settings (إذا كنت تستخدمها)
PAYTABS_DOMAIN=your-paytabs-domain

# OAuth Settings (إذا كنت تستخدمها)
META_CLIENT_ID=your-meta-client-id
META_CLIENT_SECRET=your-meta-client-secret
TIKTOK_CLIENT_ID=your-tiktok-client-id
TIKTOK_CLIENT_SECRET=your-tiktok-client-secret
```

#### 4.2 توليد SECRET_KEY
```bash
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

انسخ الناتج إلى ملف `.env` في `SECRET_KEY`.

### الخطوة 5: تحديث إعدادات Django للإنتاج

#### 5.1 تحديث ALLOWED_HOSTS
تأكد من أن `settings.py` يحتوي على:
```python
ALLOWED_HOSTS = [
    'api.loop-crm.app',
    'www.api.loop-crm.app',
    'localhost',
    '127.0.0.1',
]
```

أو استخدم متغير البيئة:
```python
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '').split(',') if os.getenv('ALLOWED_HOSTS') else []
```

#### 5.2 تحديث CORS settings
تأكد من إضافة النطاقات الجديدة في `CORS_ALLOWED_ORIGIN_REGEXES`:
```python
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://.*\.loop-crm\.app$",
    r"^https://loop-crm\.app$",
    # ... باقي الإعدادات
]
```

### الخطوة 6: إعداد قاعدة البيانات

#### 6.1 تشغيل Migrations
```bash
cd /var/www/crm-api
source venv/bin/activate
python manage.py migrate
```

#### 6.2 جمع الملفات الثابتة
```bash
python manage.py collectstatic --noinput
```

#### 6.3 إنشاء مستخدم superuser (اختياري)
```bash
python manage.py createsuperuser
```

### الخطوة 7: إعداد Gunicorn

#### 7.1 اختبار Gunicorn
```bash
cd /var/www/crm-api
source venv/bin/activate
gunicorn crm_saas_api.wsgi:application --bind 127.0.0.1:8000
```

افتح متصفح جديد وانتقل إلى `http://your-server-ip:8000` للتحقق. اضغط `Ctrl+C` لإيقاف الخادم.

#### 7.2 إنشاء ملف إعدادات Gunicorn
```bash
nano /var/www/crm-api/gunicorn_config.py
```

أضف المحتوى التالي:
```python
import multiprocessing

bind = "127.0.0.1:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 30
keepalive = 2
max_requests = 1000
max_requests_jitter = 50
preload_app = True
```

### الخطوة 8: إعداد Systemd Service

#### 8.1 إنشاء ملف service
```bash
sudo nano /etc/systemd/system/crm-api.service
```

أضف المحتوى التالي:
```ini
[Unit]
Description=CRM API Gunicorn daemon
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/crm-api
Environment="PATH=/var/www/crm-api/venv/bin"
ExecStart=/var/www/crm-api/venv/bin/gunicorn \
    --config /var/www/crm-api/gunicorn_config.py \
    crm_saas_api.wsgi:application

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

#### 8.2 تفعيل وتشغيل الخدمة
```bash
sudo systemctl daemon-reload
sudo systemctl enable crm-api
sudo systemctl start crm-api
sudo systemctl status crm-api
```

### الخطوة 9: إعداد Nginx

#### 9.1 إنشاء ملف إعدادات Nginx
```bash
sudo nano /etc/nginx/sites-available/crm-api
```

أضف المحتوى التالي:
```nginx
server {
    listen 80;
    server_name api.loop-crm.app www.api.loop-crm.app;

    # إعادة التوجيه إلى HTTPS (بعد إعداد SSL)
    # return 301 https://$server_name$request_uri;

    client_max_body_size 10M;

    # إعدادات الضغط
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/x-javascript application/xml+rss application/json;

    # توجيه الطلبات إلى Gunicorn
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        
        # إعدادات timeout
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # الملفات الثابتة
    location /static/ {
        alias /var/www/crm-api/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # الملفات المرفوعة
    location /media/ {
        alias /var/www/crm-api/media/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # منع الوصول إلى الملفات المخفية
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
```

#### 9.2 تفعيل الموقع
```bash
sudo ln -s /etc/nginx/sites-available/crm-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### الخطوة 10: إعداد SSL (HTTPS)

#### 10.1 تثبيت Certbot
```bash
sudo apt install certbot python3-certbot-nginx -y
```

#### 10.2 الحصول على شهادة SSL
```bash
sudo certbot --nginx -d api.loop-crm.app -d www.api.loop-crm.app
```

سيقوم Certbot بتحديث إعدادات Nginx تلقائياً لدعم HTTPS.

#### 10.3 تفعيل إعادة التوجيه إلى HTTPS
بعد إعداد SSL، قم بتفعيل السطر في ملف Nginx:
```nginx
return 301 https://$server_name$request_uri;
```

ثم أعد تحميل Nginx:
```bash
sudo systemctl reload nginx
```

### الخطوة 11: إعدادات الأمان

#### 11.1 إعداد جدار الحماية
```bash
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

#### 11.2 تحديث أذونات الملفات
```bash
sudo chown -R www-data:www-data /var/www/crm-api
sudo chmod -R 755 /var/www/crm-api
sudo chmod 600 /var/www/crm-api/.env  # حماية ملف .env
```

## إعداد المهام المجدولة (Cron Jobs)

### إيقاف الاشتراكات المنتهية تلقائياً

لإيقاف الاشتراكات التي انتهت صلاحيتها تلقائياً، قم بإعداد cron job:

#### 1. فتح crontab
```bash
crontab -e
```

#### 2. إضافة المهام المجدولة
اختر أحد الخيارات التالية:

**إيقاف الاشتراكات المنتهية - تشغيل كل ساعة:**
```bash
0 * * * * cd /var/www/crm-api && /var/www/crm-api/venv/bin/python manage.py end_expired_subscriptions >> /var/log/crm-api-subscriptions.log 2>&1
```

**إيقاف الاشتراكات المنتهية - تشغيل كل 15 دقيقة (موصى به للدقة):**
```bash
*/15 * * * * cd /var/www/crm-api && /var/www/crm-api/venv/bin/python manage.py end_expired_subscriptions >> /var/log/crm-api-subscriptions.log 2>&1
```

**إيقاف الاشتراكات المنتهية - تشغيل كل يوم في منتصف الليل:**
```bash
0 0 * * * cd /var/www/crm-api && /var/www/crm-api/venv/bin/python manage.py end_expired_subscriptions >> /var/log/crm-api-subscriptions.log 2>&1
```

**إرسال تذكيرات التجديد - تشغيل يومياً في الساعة 9 صباحاً:**
```bash
0 9 * * * cd /var/www/crm-api && /var/www/crm-api/venv/bin/python manage.py send_subscription_reminders >> /var/log/crm-api-reminders.log 2>&1
```

**إرسال تذكيرات التجديد - تشغيل مرتين يومياً (9 صباحاً و 6 مساءً):**
```bash
0 9,18 * * * cd /var/www/crm-api && /var/www/crm-api/venv/bin/python manage.py send_subscription_reminders >> /var/log/crm-api-reminders.log 2>&1
```

#### 3. اختبار الأوامر يدوياً
```bash
cd /var/www/crm-api
source venv/bin/activate

# اختبار إيقاف الاشتراكات المنتهية
python manage.py end_expired_subscriptions --dry-run  # للاختبار فقط
python manage.py end_expired_subscriptions  # للتطبيق الفعلي

# اختبار إرسال تذكيرات التجديد
python manage.py send_subscription_reminders --dry-run  # للاختبار فقط
python manage.py send_subscription_reminders  # للإرسال الفعلي
python manage.py send_subscription_reminders --days-before 3 --verbose  # مع خيارات إضافية
```

#### 4. مراقبة السجلات
```bash
# سجلات إيقاف الاشتراكات
tail -f /var/log/crm-api-subscriptions.log

# سجلات تذكيرات التجديد
tail -f /var/log/crm-api-reminders.log
```

## صيانة وتحديثات

### تحديث المشروع
```bash
cd /var/www/crm-api
source venv/bin/activate
git pull origin main  # أو رفع الملفات الجديدة
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart crm-api
```

### مراقبة السجلات
```bash
# سجلات Gunicorn
sudo journalctl -u crm-api -f

# سجلات Nginx
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log

# سجلات Django
tail -f /var/www/crm-api/logs/django.log  # إذا كنت تستخدم logging
```

### إعادة تشغيل الخدمات
```bash
# إعادة تشغيل Gunicorn
sudo systemctl restart crm-api

# إعادة تشغيل Nginx
sudo systemctl restart nginx
```

## استكشاف الأخطاء

### المشكلة: خطأ 502 Bad Gateway
```bash
# تحقق من حالة Gunicorn
sudo systemctl status crm-api

# تحقق من السجلات
sudo journalctl -u crm-api -n 50

# تحقق من أن Gunicorn يعمل على المنفذ الصحيح
sudo netstat -tlnp | grep 8000
```

### المشكلة: خطأ 500 Internal Server Error
```bash
# تحقق من إعدادات Django
cd /var/www/crm-api
source venv/bin/activate
python manage.py check --deploy

# تحقق من ملف .env
cat .env | grep -v SECRET_KEY

# تحقق من قاعدة البيانات
python manage.py dbshell
```

### المشكلة: CORS errors
- تأكد من إضافة النطاقات في `CORS_ALLOWED_ORIGIN_REGEXES`
- تأكد من أن `CORS_ALLOW_ALL_ORIGINS=False` في الإنتاج
- أعد تشغيل Gunicorn بعد التعديلات

### المشكلة: الملفات الثابتة لا تُحمّل
```bash
# جمع الملفات الثابتة مرة أخرى
cd /var/www/crm-api
source venv/bin/activate
python manage.py collectstatic --noinput

# تحقق من الأذونات
sudo chown -R www-data:www-data /var/www/crm-api/staticfiles
```

## نصائح إضافية

1. **النسخ الاحتياطي**: قم بعمل نسخ احتياطي دوري من قاعدة البيانات وملف `.env`
2. **مراقبة الأداء**: استخدم أدوات مثل `htop` و `iotop` لمراقبة الموارد
3. **تحديثات الأمان**: قم بتحديث النظام و Python packages بانتظام
4. **مراقبة SSL**: تأكد من تجديد شهادة SSL قبل انتهائها (Certbot يقوم بذلك تلقائياً)
5. **ترقية قاعدة البيانات**: فكر في ترقية SQLite إلى PostgreSQL أو MySQL للإنتاج

## هيكل الملفات النهائي

```
/var/www/crm-api/
├── venv/              # Virtual environment
├── staticfiles/       # الملفات الثابتة المجمعة
├── media/             # الملفات المرفوعة
├── db.sqlite3         # قاعدة البيانات
├── .env               # متغيرات البيئة
├── gunicorn_config.py # إعدادات Gunicorn
├── manage.py
└── ...               # باقي ملفات المشروع
```

## الدعم

إذا واجهت أي مشاكل، تحقق من:
- سجلات Gunicorn: `sudo journalctl -u crm-api -f`
- سجلات Nginx: `sudo tail -f /var/log/nginx/error.log`
- إعدادات Django: `python manage.py check --deploy`
- متغيرات البيئة في ملف `.env`

