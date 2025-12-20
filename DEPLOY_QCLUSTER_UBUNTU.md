# كيفية تشغيل Django Q Cluster على Ubuntu VPS

## الطريقة 1: استخدام systemd (موصى به)

### 1. إنشاء ملف الخدمة

أنشئ ملف خدمة systemd:

```bash
sudo nano /etc/systemd/system/django-q-cluster.service
```

### 2. محتوى ملف الخدمة

```ini
[Unit]
Description=Django Q Cluster for CRM
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/path/to/your/CRM-api-1
Environment="PATH=/path/to/your/venv/bin"
ExecStart=/path/to/your/venv/bin/python manage.py qcluster
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**ملاحظات مهمة:**
- استبدل `/path/to/your/CRM-api-1` بمسار مشروعك الفعلي
- استبدل `/path/to/your/venv/bin` بمسار virtual environment الخاص بك
- يمكنك تغيير `User=www-data` إلى المستخدم الذي تشغّل به Django

### 3. تفعيل وتشغيل الخدمة

```bash
# إعادة تحميل systemd
sudo systemctl daemon-reload

# تفعيل الخدمة لتبدأ تلقائياً عند إعادة التشغيل
sudo systemctl enable django-q-cluster.service

# بدء الخدمة
sudo systemctl start django-q-cluster.service

# التحقق من حالة الخدمة
sudo systemctl status django-q-cluster.service

# عرض السجلات
sudo journalctl -u django-q-cluster.service -f
```

### 4. أوامر إدارة الخدمة

```bash
# إيقاف الخدمة
sudo systemctl stop django-q-cluster.service

# إعادة تشغيل الخدمة
sudo systemctl restart django-q-cluster.service

# تعطيل الخدمة (لن تبدأ تلقائياً)
sudo systemctl disable django-q-cluster.service

# عرض السجلات
sudo journalctl -u django-q-cluster.service
sudo journalctl -u django-q-cluster.service -n 100  # آخر 100 سطر
```

---

## الطريقة 2: استخدام Supervisor (بديل)

### 1. تثبيت Supervisor

```bash
sudo apt-get update
sudo apt-get install supervisor
```

### 2. إنشاء ملف الإعداد

```bash
sudo nano /etc/supervisor/conf.d/django-q-cluster.conf
```

### 3. محتوى ملف الإعداد

```ini
[program:django-q-cluster]
command=/path/to/your/venv/bin/python /path/to/your/CRM-api-1/manage.py qcluster
directory=/path/to/your/CRM-api-1
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/django-q-cluster.log
stderr_logfile=/var/log/django-q-cluster-error.log
environment=PATH="/path/to/your/venv/bin"
```

### 4. تفعيل وتشغيل Supervisor

```bash
# إعادة تحميل Supervisor
sudo supervisorctl reread
sudo supervisorctl update

# بدء الخدمة
sudo supervisorctl start django-q-cluster

# التحقق من الحالة
sudo supervisorctl status django-q-cluster

# عرض السجلات
sudo tail -f /var/log/django-q-cluster.log
```

### 5. أوامر إدارة Supervisor

```bash
# إيقاف
sudo supervisorctl stop django-q-cluster

# إعادة تشغيل
sudo supervisorctl restart django-q-cluster

# عرض الحالة
sudo supervisorctl status django-q-cluster
```

---

## الطريقة 3: استخدام screen أو tmux (للتطوير/الاختبار)

### باستخدام screen:

```bash
# تثبيت screen
sudo apt-get install screen

# إنشاء جلسة جديدة
screen -S django-q-cluster

# تشغيل qcluster
cd /path/to/your/CRM-api-1
source venv/bin/activate
python manage.py qcluster

# للانفصال: اضغط Ctrl+A ثم D
# للعودة: screen -r django-q-cluster
```

### باستخدام tmux:

```bash
# تثبيت tmux
sudo apt-get install tmux

# إنشاء جلسة جديدة
tmux new -s django-q-cluster

# تشغيل qcluster
cd /path/to/your/CRM-api-1
source venv/bin/activate
python manage.py qcluster

# للانفصال: اضغط Ctrl+B ثم D
# للعودة: tmux attach -t django-q-cluster
```

---

## إعدادات مهمة في settings.py

تأكد من أن إعدادات Django Q صحيحة للإنتاج:

```python
Q_CLUSTER = {
    'name': 'CRM_Queue',
    'workers': 4,  # عدد العمال (يمكن زيادته حسب احتياجاتك)
    'recycle': 500,
    'timeout': 60,
    'retry': 120,
    'queue_limit': 50,
    'bulk': 10,
    'orm': 'default',
    'catch_up': False,
    'sync': False,
}
```

---

## نصائح للإنتاج

1. **استخدام قاعدة بيانات منفصلة للـ Queue:**
   ```python
   Q_CLUSTER = {
       # ...
       'orm': 'default',  # أو قاعدة بيانات منفصلة
   }
   ```

2. **مراقبة الأداء:**
   - راقب استخدام الذاكرة والـ CPU
   - راقب السجلات بانتظام
   - تأكد من أن الخدمة تعمل دائماً

3. **النسخ الاحتياطي:**
   - تأكد من نسخ قاعدة البيانات التي تستخدمها django-q2
   - السجلات مهمة أيضاً للتصحيح

4. **الأمان:**
   - استخدم مستخدم منفصل (ليس root)
   - قيّد الصلاحيات حسب الحاجة

---

## استكشاف الأخطاء

### المشكلة: الخدمة لا تبدأ

```bash
# تحقق من السجلات
sudo journalctl -u django-q-cluster.service -n 50

# تحقق من المسارات
which python
which manage.py

# تحقق من الصلاحيات
ls -la /path/to/your/CRM-api-1
```

### المشكلة: الخدمة تتوقف فجأة

```bash
# تحقق من الذاكرة
free -h

# تحقق من السجلات
sudo journalctl -u django-q-cluster.service --since "1 hour ago"
```

### المشكلة: المهام لا تعمل

```bash
# تحقق من أن الجدولة موجودة
python manage.py shell
>>> from django_q.models import Schedule
>>> Schedule.objects.all()

# تحقق من حالة الـ cluster
python manage.py qmonitor
```

---

## مثال كامل لملف systemd

```ini
[Unit]
Description=Django Q Cluster for CRM API
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/CRM/CRM-api-1
Environment="PATH=/home/ubuntu/CRM/CRM-api-1/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DJANGO_SETTINGS_MODULE=crm_saas_api.settings"
ExecStart=/home/ubuntu/CRM/CRM-api-1/venv/bin/python /home/ubuntu/CRM/CRM-api-1/manage.py qcluster
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## التحقق من أن كل شيء يعمل

```bash
# 1. تحقق من حالة الخدمة
sudo systemctl status django-q-cluster.service

# 2. تحقق من أن qcluster يعمل
ps aux | grep qcluster

# 3. تحقق من الجدولات
python manage.py shell
>>> from django_q.models import Schedule
>>> for s in Schedule.objects.all():
...     print(f"{s.name}: {s.func} - Next run: {s.next_run}")

# 4. تحقق من المهام المكتملة
>>> from django_q.models import Success
>>> Success.objects.all().order_by('-stopped')[:5]
```

---

## ملاحظات إضافية

- تأكد من أن قاعدة البيانات متاحة قبل بدء qcluster
- في حالة استخدام Nginx/Gunicorn، تأكد من أن qcluster يعمل بشكل منفصل
- يمكنك تشغيل عدة instances من qcluster على نفس الخادم (مع أسماء مختلفة)
- راقب استخدام الموارد (CPU, Memory) وعدّل عدد العمال حسب الحاجة

