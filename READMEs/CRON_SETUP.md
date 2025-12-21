# إعداد Cron Job لإعادة التعيين (بدلاً من django-q2)

## لماذا استخدام Cron؟

إذا كان لديك بالفعل cron jobs أخرى في التطبيق، فاستخدام cron أسهل وأبسط من django-q2:
- لا حاجة لتشغيل خدمة منفصلة (qcluster)
- إدارة أبسط
- أقل استهلاكاً للموارد
- أسهل في المراقبة والتصحيح

## الخطوات

### 1. إزالة django-q2 (اختياري)

إذا كنت تريد إزالة django-q2 تماماً:

```bash
# إزالة من INSTALLED_APPS في settings.py
# إزالة 'django_q' من القائمة

# إزالة من requirements.txt (اختياري)
# pip uninstall django-q2
```

**ملاحظة:** يمكنك الاحتفاظ بـ django-q2 إذا كنت تريد استخدامه لاحقاً، فقط لا تشغّل qcluster.

### 2. إضافة Cron Job

افتح crontab:

```bash
crontab -e
```

أضف السطر التالي لتشغيل المهمة كل ساعة:

```cron
# تشغيل مهمة إعادة التعيين كل ساعة
0 * * * * cd /path/to/your/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1
```

**أمثلة أخرى:**

```cron
# كل 30 دقيقة
*/30 * * * * cd /path/to/your/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1

# كل ساعتين
0 */2 * * * cd /path/to/your/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1

# كل يوم في الساعة 2 صباحاً
0 2 * * * cd /path/to/your/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1
```

### 3. تعديل المسارات

استبدل:
- `/path/to/your/CRM-api-1` → المسار الفعلي لمشروعك
- `/path/to/venv/bin/python` → مسار Python في virtual environment

**مثال:**

```cron
0 * * * * cd /home/ubuntu/CRM/CRM-api-1 && /home/ubuntu/CRM/CRM-api-1/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1
```

### 4. إضافة متغيرات البيئة (إذا لزم الأمر)

إذا كنت تستخدم متغيرات بيئة (مثل .env):

```cron
0 * * * * cd /path/to/your/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1
```

أو إذا كنت تستخدم environment variables:

```cron
0 * * * * cd /path/to/your/CRM-api-1 && export DJANGO_SETTINGS_MODULE=crm_saas_api.settings && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm_reassign.log 2>&1
```

### 5. إنشاء ملف log (اختياري)

```bash
sudo touch /var/log/crm_reassign.log
sudo chmod 666 /var/log/crm_reassign.log
```

أو استخدم مسار في مجلد المشروع:

```cron
0 * * * * cd /path/to/your/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /path/to/your/CRM-api-1/logs/reassign.log 2>&1
```

### 6. التحقق من Cron Job

```bash
# عرض cron jobs الحالية
crontab -l

# اختبار الأمر يدوياً
cd /path/to/your/CRM-api-1
/path/to/venv/bin/python manage.py run_reassign_task

# مراقبة السجلات
tail -f /var/log/crm_reassign.log
```

## مثال كامل

```cron
# ============================================
# CRM Re-assign Task - كل ساعة
# ============================================
0 * * * * cd /home/ubuntu/CRM/CRM-api-1 && /home/ubuntu/CRM/CRM-api-1/venv/bin/python manage.py run_reassign_task >> /home/ubuntu/CRM/CRM-api-1/logs/reassign.log 2>&1
```

## إدارة Cron Jobs

```bash
# عرض جميع cron jobs
crontab -l

# تعديل cron jobs
crontab -e

# حذف جميع cron jobs
crontab -r

# إضافة cron job من ملف
crontab /path/to/cronfile.txt
```

## مراقبة وتصحيح

### 1. التحقق من أن Cron يعمل

```bash
# تحقق من أن cron service يعمل
sudo systemctl status cron

# أو في بعض الأنظمة
sudo systemctl status crond
```

### 2. عرض السجلات

```bash
# سجلات cron العامة
grep CRON /var/log/syslog

# سجلات المهمة الخاصة
tail -f /var/log/crm_reassign.log
```

### 3. اختبار يدوي

```bash
cd /path/to/your/CRM-api-1
source venv/bin/activate
python manage.py run_reassign_task
```

## مقارنة: Cron vs Django-Q2

| الميزة | Cron | Django-Q2 |
|--------|------|-----------|
| سهولة الإعداد | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| إدارة المهام | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| واجهة إدارة | ❌ | ✅ (Django Admin) |
| إعادة المحاولة التلقائية | ❌ | ✅ |
| مراقبة المهام | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| استهلاك الموارد | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| مناسب للمهام البسيطة | ✅ | ❌ |
| مناسب للمهام المعقدة | ❌ | ✅ |

## التوصية

**استخدم Cron إذا:**
- لديك بالفعل cron jobs أخرى
- المهمة بسيطة (مثل re-assign)
- تريد حل أبسط
- لا تحتاج واجهة إدارة

**استخدم Django-Q2 إذا:**
- تحتاج إدارة متقدمة للمهام
- تحتاج إعادة محاولة تلقائية
- تريد مراقبة المهام من Django Admin
- لديك مهام معقدة متعددة

## ملاحظات مهمة

1. **توقيت Cron:** تأكد من أن الساعة في الخادم صحيحة
   ```bash
   date
   sudo timedatectl set-timezone Asia/Riyadh  # مثال
   ```

2. **البيئة:** تأكد من أن cron job يستخدم نفس البيئة (venv, settings) التي يستخدمها Django

3. **الصلاحيات:** تأكد من أن المستخدم الذي يشغّل cron لديه صلاحيات الوصول للمشروع

4. **السجلات:** راقب السجلات بانتظام للتأكد من أن المهمة تعمل

