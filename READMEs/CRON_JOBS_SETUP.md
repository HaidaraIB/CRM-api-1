# دليل إعداد Cron Jobs للمشروع

هذا الملف يوضح كيفية إعداد جميع Cron Jobs المطلوبة لتشغيل المهام المجدولة في المشروع.

## 📋 المحتويات

1. [المتطلبات الأساسية](#المتطلبات-الأساسية)
2. [إعداد Cron Jobs](#إعداد-cron-jobs)
3. [قائمة جميع Cron Jobs](#قائمة-جميع-cron-jobs)
4. [إعداد Crontab](#إعداد-crontab)
5. [التحقق من عمل Cron Jobs](#التحقق-من-عمل-cron-jobs)
6. [استكشاف الأخطاء](#استكشاف-الأخطاء)

---

## المتطلبات الأساسية

قبل إعداد Cron Jobs، تأكد من:

1. **Python و Django مثبتان بشكل صحيح**
2. **المشروع يعمل بشكل صحيح**
3. **قاعدة البيانات متصلة ومهاجرة**
4. **متغيرات البيئة (Environment Variables) مضبوطة بشكل صحيح**

### المسار الأساسي للمشروع

```bash
PROJECT_PATH="/path/to/CRM-api-1"
# أو في Windows
PROJECT_PATH="C:\Users\ASUS\Desktop\CRM\CRM-api-1"
```

### تفعيل البيئة الافتراضية (Virtual Environment)

```bash
# Linux/Mac
source venv/bin/activate

# Windows
venv\Scripts\activate
```

---

## إعداد Cron Jobs

### على Linux/Mac

استخدم `crontab -e` لفتح محرر cron:

```bash
crontab -e
```

### على Windows

استخدم **Task Scheduler** أو **WSL** (Windows Subsystem for Linux).

---

## قائمة جميع Cron Jobs

### 1. إرسال البث المجدول (Scheduled Broadcasts)

**الوصف:** يفحص ويرسل البث الإلكتروني المجدول عند الوقت المحدد.

**الأهمية:** ⭐⭐⭐⭐⭐ (عالية جداً)

**التكرار المقترح:** كل دقيقة

**Command:**
```bash
* * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_scheduled_broadcasts
```

**مع الخيارات:**
```bash
# مع verbose mode
* * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_scheduled_broadcasts --verbose

# للاختبار (dry-run)
* * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_scheduled_broadcasts --dry-run
```

**الخيارات المتاحة:**
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي
- `--verbose`: عرض تفاصيل لكل بث
- `--check-minutes N`: فحص البث المجدول في آخر N دقائق (افتراضي: 1)

---

### 2. إرسال تذكيرات انتهاء الاشتراك (Subscription Reminders)

**الوصف:** يرسل رسائل تذكير للمستخدمين قبل 3 أيام من انتهاء اشتراكهم.

**الأهمية:** ⭐⭐⭐⭐ (عالية)

**التكرار المقترح:** يومياً في الساعة 9 صباحاً

**Command:**
```bash
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_subscription_reminders
```

**مع الخيارات:**
```bash
# تذكير قبل 5 أيام بدلاً من 3
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_subscription_reminders --days-before 5

# مع verbose mode
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_subscription_reminders --verbose

# للاختبار
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_subscription_reminders --dry-run
```

**الخيارات المتاحة:**
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي
- `--days-before N`: عدد الأيام قبل انتهاء الاشتراك لإرسال التذكير (افتراضي: 3)
- `--verbose`: عرض تفاصيل لكل اشتراك

---

### 3. إنهاء الاشتراكات المنتهية (End Expired Subscriptions)

**الوصف:** ينهي الاشتراكات التي وصلت إلى تاريخ انتهائها تلقائياً.

**الأهمية:** ⭐⭐⭐⭐⭐ (عالية جداً)

**التكرار المقترح:** كل ساعة أو كل 15 دقيقة

**Command (كل ساعة):**
```bash
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py end_expired_subscriptions
```

**Command (كل 15 دقيقة):**
```bash
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py end_expired_subscriptions
```

**مع الخيارات:**
```bash
# مع verbose mode
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py end_expired_subscriptions --verbose

# للاختبار
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py end_expired_subscriptions --dry-run
```

**الخيارات المتاحة:**
- `--dry-run`: عرض ما سيتم تحديثه دون تحديث فعلي
- `--verbose`: عرض تفاصيل لكل اشتراك

---

### 4. إعادة تعيين العملاء غير النشطين (Re-assign Inactive Clients)

**الوصف:** يعيد تعيين العملاء غير النشطين للموظفين بناءً على إعدادات auto_assign.

**الأهمية:** ⭐⭐⭐ (متوسطة)

**ملاحظة:** يمكن استخدام Django-Q2 بدلاً من Cron لهذه المهمة.

**التكرار المقترح:** كل ساعة

**Command:**
```bash
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task
```

**بديل: استخدام Django-Q2 (مُوصى به):**
```bash
# قم بتشغيل هذا الأمر مرة واحدة فقط لإعداد الجدولة
python manage.py setup_reassign_schedule
```

---

### 5. تعيين العملاء غير المعينين (Assign Unassigned Clients)

**الوصف:** يعين العملاء غير المعينين للموظفين بناءً على إعدادات auto_assign.

**الأهمية:** ⭐⭐⭐ (متوسطة)

**التكرار المقترح:** يومياً في الساعة 8 صباحاً

**Command:**
```bash
0 8 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py assign_unassigned_clients
```

**مع الخيارات:**
```bash
# لشركة محددة
0 8 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py assign_unassigned_clients --company-id 1

# للاختبار
0 8 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py assign_unassigned_clients --dry-run
```

**الخيارات المتاحة:**
- `--company-id N`: معالجة عملاء شركة محددة فقط
- `--dry-run`: عرض ما سيتم تعيينه دون تعيين فعلي

---

### 6. تنظيف التسجيلات غير المكتملة (Cleanup Incomplete Registrations)

**الوصف:** يحذف الشركات والمستخدمين والاشتراكات التي تم إنشاؤها ولكن لم تكمل الدفع خلال 48 ساعة.

**الأهمية:** ⭐⭐ (منخفضة)

**التكرار المقترح:** يومياً في الساعة 2 صباحاً

**Command:**
```bash
0 2 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py cleanup_incomplete_registrations
```

**مع الخيارات:**
```bash
# تنظيف التسجيلات الأقدم من 24 ساعة
0 2 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py cleanup_incomplete_registrations --hours 24

# للاختبار
0 2 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py cleanup_incomplete_registrations --dry-run
```

**الخيارات المتاحة:**
- `--hours N`: عدد الساعات بعدها يتم حذف التسجيلات غير المكتملة (افتراضي: 48)
- `--dry-run`: عرض ما سيتم حذفه دون حذف فعلي

---

## 🔔 Cron Jobs للإشعارات (Notification Cron Jobs)

### 7. فحص Leads بدون متابعة (Check Lead No Follow Up)

**الوصف:** يفحص العملاء المحتملين الذين لم يتم التواصل معهم منذ فترة معينة ويرسل إشعارات.

**الأهمية:** ⭐⭐⭐⭐ (عالية)

**التكرار المقترح:** كل 30 دقيقة

**Command:**
```bash
*/30 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_no_follow_up
```

**مع الخيارات:**
```bash
# فحص Leads بدون متابعة لمدة 60 دقيقة
*/30 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_no_follow_up --minutes 60

# للاختبار
*/30 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_no_follow_up --dry-run
```

**الخيارات المتاحة:**
- `--minutes N`: عدد الدقائق بدون متابعة لإرسال الإشعار (افتراضي: 30)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 8. فحص تذكيرات Leads (Check Lead Reminders)

**الوصف:** يفحص تذكيرات متابعة العملاء المحتملين ويرسل إشعارات قبل الموعد.

**الأهمية:** ⭐⭐⭐⭐ (عالية)

**التكرار المقترح:** كل 15 دقيقة

**Command:**
```bash
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_reminders
```

**مع الخيارات:**
```bash
# إرسال التذكير قبل 60 دقيقة من الموعد
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_reminders --minutes-before 60

# للاختبار
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_reminders --dry-run
```

**الخيارات المتاحة:**
- `--minutes-before N`: عدد الدقائق قبل موعد التذكير لإرسال الإشعار (افتراضي: 30)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 9. فحص رسائل واتساب بانتظار الرد (Check WhatsApp Waiting Response)

**الوصف:** يفحص رسائل واتساب المرسلة التي بانتظار رد من العميل ويرسل إشعارات.

**الأهمية:** ⭐⭐⭐ (متوسطة)

**التكرار المقترح:** كل ساعة

**Command:**
```bash
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_whatsapp_waiting_response
```

**مع الخيارات:**
```bash
# فحص رسائل بانتظار رد لمدة 48 ساعة
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_whatsapp_waiting_response --hours 48

# للاختبار
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_whatsapp_waiting_response --dry-run
```

**الخيارات المتاحة:**
- `--hours N`: عدد الساعات بدون رد لإرسال الإشعار (افتراضي: 24)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

**ملاحظة:** يتطلب تتبع `last_message_sent_at` في Client model أو استخدام `last_contacted_at` كبديل.

---

### 10. فحص أداء الحملات الإعلانية (Check Campaign Performance)

**الوصف:** يفحص أداء الحملات الإعلانية ويرسل إشعارات عند انخفاض الأداء أو انخفاض الميزانية.

**الأهمية:** ⭐⭐⭐⭐ (عالية)

**التكرار المقترح:** يومياً في الساعة 10 صباحاً

**Command:**
```bash
0 10 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_campaign_performance
```

**مع الخيارات:**
```bash
# فحص انخفاض الأداء فقط
0 10 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_campaign_performance --check-low-performance

# فحص تنبيهات الميزانية فقط
0 10 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_campaign_performance --check-budget-alert --budget-threshold 15

# للاختبار
0 10 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_campaign_performance --dry-run
```

**الخيارات المتاحة:**
- `--check-low-performance`: فحص الحملات منخفضة الأداء
- `--check-budget-alert`: فحص الحملات مع ميزانية منخفضة
- `--budget-threshold N`: نسبة الميزانية المتبقية للتنبيه (افتراضي: 20%)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

**ملاحظة:** يتطلب تتبع `spent` في Campaign model.

---

### 11. فحص تذكيرات المهام (Check Task Reminders)

**الوصف:** يفحص تذكيرات المهام ويرسل إشعارات قبل الموعد.

**الأهمية:** ⭐⭐⭐ (متوسطة)

**التكرار المقترح:** كل 15 دقيقة

**Command:**
```bash
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_task_reminders
```

**مع الخيارات:**
```bash
# إرسال التذكير قبل 60 دقيقة من الموعد
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_task_reminders --minutes-before 60

# للاختبار
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_task_reminders --dry-run
```

**الخيارات المتاحة:**
- `--minutes-before N`: عدد الدقائق قبل موعد التذكير لإرسال الإشعار (افتراضي: 30)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 12. فحص تذكيرات الصفقات (Check Deal Reminders)

**الوصف:** يفحص تذكيرات الصفقات ويرسل إشعارات قبل الموعد.

**الأهمية:** ⭐⭐⭐ (متوسطة)

**التكرار المقترح:** كل ساعة

**Command:**
```bash
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_deal_reminders
```

**مع الخيارات:**
```bash
# إرسال التذكير قبل 2 ساعة من الموعد
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_deal_reminders --hours-before 2

# للاختبار
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_deal_reminders --dry-run
```

**الخيارات المتاحة:**
- `--hours-before N`: عدد الساعات قبل موعد التذكير لإرسال الإشعار (افتراضي: 1)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

**ملاحظة:** يستخدم `start_date` في Deal model حالياً. يمكن إضافة `reminder_date` field لاحقاً.

---

### 13. إرسال التقارير اليومية (Send Daily Reports)

**الوصف:** يرسل تقارير يومية للأداء لمالكي الشركات.

**الأهمية:** ⭐⭐⭐⭐ (عالية)

**التكرار المقترح:** يومياً في الساعة 9 صباحاً

**Command:**
```bash
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_daily_report
```

**مع الخيارات:**
```bash
# إرسال تقرير لشركة محددة
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_daily_report --company-id 1

# إرسال تقرير لتاريخ محدد
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_daily_report --date 2024-01-15

# للاختبار
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_daily_report --dry-run
```

**الخيارات المتاحة:**
- `--company-id N`: إرسال تقرير لشركة محددة فقط
- `--date YYYY-MM-DD`: تاريخ التقرير (افتراضي: اليوم)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 14. إرسال التقارير الأسبوعية (Send Weekly Reports)

**الوصف:** يرسل تقارير أسبوعية للأداء لمالكي الشركات.

**الأهمية:** ⭐⭐⭐⭐ (عالية)

**التكرار المقترح:** أسبوعياً يوم الاثنين في الساعة 9 صباحاً

**Command:**
```bash
0 9 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_weekly_report
```

**مع الخيارات:**
```bash
# إرسال تقرير لشركة محددة
0 9 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_weekly_report --company-id 1

# تقرير آخر 14 يوم
0 9 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_weekly_report --days 14

# للاختبار
0 9 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_weekly_report --dry-run
```

**الخيارات المتاحة:**
- `--company-id N`: إرسال تقرير لشركة محددة فقط
- `--days N`: عدد الأيام لتضمينها في التقرير (افتراضي: 7)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 15. إرسال إشعارات أفضل موظف (Send Top Employee Notifications)

**الوصف:** يحسب أفضل موظف مبيعات للأسبوع ويرسل إشعارات.

**الأهمية:** ⭐⭐⭐ (متوسطة)

**التكرار المقترح:** أسبوعياً يوم الاثنين في الساعة 10 صباحاً

**Command:**
```bash
0 10 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_top_employee_notification
```

**مع الخيارات:**
```bash
# إرسال إشعار لشركة محددة
0 10 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_top_employee_notification --company-id 1

# حساب أفضل موظف آخر 14 يوم
0 10 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_top_employee_notification --days 14

# للاختبار
0 10 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_top_employee_notification --dry-run
```

**الخيارات المتاحة:**
- `--company-id N`: إرسال إشعار لشركة محددة فقط
- `--days N`: عدد الأيام لحساب أفضل موظف (افتراضي: 7)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 16. فحص الاشتراكات المنتهية قريباً (Check Subscription Expiring)

**الوصف:** يفحص الاشتراكات التي ستنتهي قريباً ويرسل إشعارات.

**الأهمية:** ⭐⭐⭐⭐⭐ (عالية جداً)

**التكرار المقترح:** يومياً في الساعة 9 صباحاً

**Command:**
```bash
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expiring
```

**مع الخيارات:**
```bash
# تذكير قبل 5 أيام بدلاً من 3
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expiring --days-before 5

# للاختبار
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expiring --dry-run
```

**الخيارات المتاحة:**
- `--days-before N`: عدد الأيام قبل انتهاء الاشتراك لإرسال الإشعار (افتراضي: 3)
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

### 17. فحص الاشتراكات المنتهية (Check Subscription Expired)

**الوصف:** يفحص الاشتراكات المنتهية ويرسل إشعارات ويمكنه إلغاء تفعيلها تلقائياً.

**الأهمية:** ⭐⭐⭐⭐⭐ (عالية جداً)

**التكرار المقترح:** يومياً في منتصف الليل

**Command:**
```bash
0 0 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expired --deactivate
```

**مع الخيارات:**
```bash
# إرسال إشعارات فقط دون إلغاء التفعيل
0 0 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expired

# إرسال إشعارات وإلغاء تفعيل الاشتراكات المنتهية
0 0 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expired --deactivate

# للاختبار
0 0 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expired --dry-run
```

**الخيارات المتاحة:**
- `--deactivate`: إلغاء تفعيل الاشتراكات المنتهية تلقائياً
- `--dry-run`: عرض ما سيتم إرساله دون إرسال فعلي

---

## إعداد Crontab

### مثال كامل لملف Crontab

```bash
# ============================================
# CRM Project - Cron Jobs Configuration
# ============================================

# متغيرات البيئة
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO=admin@example.com

# تفعيل البيئة الافتراضية (إذا لزم الأمر)
# source /path/to/venv/bin/activate

# ============================================
# مهام عالية الأهمية - تشغيل متكرر
# ============================================

# 1. إرسال البث المجدول - كل دقيقة
* * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_scheduled_broadcasts >> /var/log/crm/send_scheduled_broadcasts.log 2>&1

# 2. إنهاء الاشتراكات المنتهية - كل ساعة
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py end_expired_subscriptions >> /var/log/crm/end_expired_subscriptions.log 2>&1

# ============================================
# مهام يومية
# ============================================

# 3. إرسال تذكيرات انتهاء الاشتراك - يومياً في 9 صباحاً
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_subscription_reminders >> /var/log/crm/send_subscription_reminders.log 2>&1

# 4. تعيين العملاء غير المعينين - يومياً في 8 صباحاً
0 8 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py assign_unassigned_clients >> /var/log/crm/assign_unassigned_clients.log 2>&1

# 5. تنظيف التسجيلات غير المكتملة - يومياً في 2 صباحاً
0 2 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py cleanup_incomplete_registrations >> /var/log/crm/cleanup_incomplete_registrations.log 2>&1

# ============================================
# مهام اختيارية (إذا لم تستخدم Django-Q2)
# ============================================

# 6. إعادة تعيين العملاء غير النشطين - كل ساعة (بديل لـ Django-Q2)
# 0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py run_reassign_task >> /var/log/crm/run_reassign_task.log 2>&1

# ============================================
# مهام الإشعارات (Notification Cron Jobs)
# ============================================

# 7. فحص Leads بدون متابعة - كل 30 دقيقة
*/30 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_no_follow_up >> /var/log/crm/check_lead_no_follow_up.log 2>&1

# 8. فحص تذكيرات Leads - كل 15 دقيقة
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_lead_reminders >> /var/log/crm/check_lead_reminders.log 2>&1

# 9. فحص رسائل واتساب بانتظار الرد - كل ساعة
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_whatsapp_waiting_response >> /var/log/crm/check_whatsapp_waiting_response.log 2>&1

# 10. فحص أداء الحملات الإعلانية - يومياً في 10 صباحاً
0 10 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_campaign_performance >> /var/log/crm/check_campaign_performance.log 2>&1

# 11. فحص تذكيرات المهام - كل 15 دقيقة
*/15 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_task_reminders >> /var/log/crm/check_task_reminders.log 2>&1

# 12. فحص تذكيرات الصفقات - كل ساعة
0 * * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_deal_reminders >> /var/log/crm/check_deal_reminders.log 2>&1

# 13. إرسال التقارير اليومية - يومياً في 9 صباحاً
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_daily_report >> /var/log/crm/send_daily_report.log 2>&1

# 14. إرسال التقارير الأسبوعية - أسبوعياً يوم الاثنين في 9 صباحاً
0 9 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_weekly_report >> /var/log/crm/send_weekly_report.log 2>&1

# 15. إرسال إشعارات أفضل موظف - أسبوعياً يوم الاثنين في 10 صباحاً
0 10 * * 1 cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py send_top_employee_notification >> /var/log/crm/send_top_employee_notification.log 2>&1

# 16. فحص الاشتراكات المنتهية قريباً - يومياً في 9 صباحاً
0 9 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expiring >> /var/log/crm/check_subscription_expiring.log 2>&1

# 17. فحص الاشتراكات المنتهية - يومياً في منتصف الليل
0 0 * * * cd /path/to/CRM-api-1 && /path/to/venv/bin/python manage.py check_subscription_expired --deactivate >> /var/log/crm/check_subscription_expired.log 2>&1
```

### إعداد Crontab خطوة بخطوة

1. **افتح محرر crontab:**
   ```bash
   crontab -e
   ```

2. **أضف المهام المطلوبة** (انسخ من المثال أعلاه)

3. **احفظ الملف واخرج**

4. **تحقق من المهام المضافة:**
   ```bash
   crontab -l
   ```

### إنشاء مجلدات السجلات (Logs)

```bash
sudo mkdir -p /var/log/crm
sudo chown $USER:$USER /var/log/crm
```

---

## التحقق من عمل Cron Jobs

### 1. عرض سجلات Cron

```bash
# عرض سجلات cron العامة
grep CRON /var/log/syslog

# عرض سجلات مهام محددة
tail -f /var/log/crm/send_scheduled_broadcasts.log
tail -f /var/log/crm/end_expired_subscriptions.log
```

### 2. اختبار الأوامر يدوياً

```bash
# اختبار إرسال البث المجدول
cd /path/to/CRM-api-1
python manage.py send_scheduled_broadcasts --verbose

# اختبار إرسال التذكيرات
python manage.py send_subscription_reminders --dry-run

# اختبار إنهاء الاشتراكات
python manage.py end_expired_subscriptions --dry-run
```

### 3. التحقق من حالة Cron Service

```bash
# Linux (systemd)
sudo systemctl status cron

# Linux (SysV)
sudo service cron status
```

---

## استكشاف الأخطاء

### المشاكل الشائعة وحلولها

#### 1. المهام لا تعمل

**التحقق:**
- تأكد من أن cron service يعمل
- تحقق من المسارات (paths) في crontab
- تحقق من صلاحيات الملفات

**الحل:**
```bash
# استخدام المسارات المطلقة
which python
# استخدم المسار الكامل في crontab
```

#### 2. أخطاء في البيئة الافتراضية

**التحقق:**
- تأكد من تفعيل البيئة الافتراضية في crontab
- أو استخدم المسار الكامل لـ python في البيئة الافتراضية

**الحل:**
```bash
# في crontab، استخدم المسار الكامل
/path/to/venv/bin/python manage.py command
```

#### 3. أخطاء في قاعدة البيانات

**التحقق:**
- تأكد من أن قاعدة البيانات متصلة
- تحقق من متغيرات البيئة (DATABASE_URL, etc.)

**الحل:**
```bash
# أضف متغيرات البيئة في crontab
DATABASE_URL=postgresql://... * * * * * command
```

#### 4. أخطاء في SMTP

**التحقق:**
- تأكد من إعدادات SMTP في قاعدة البيانات
- تحقق من أن SMTP مفعّل

**الحل:**
```bash
# تحقق من إعدادات SMTP
python manage.py shell
>>> from settings.models import SMTPSettings
>>> smtp = SMTPSettings.get_settings()
>>> print(smtp.is_active)
```

---

## جدول ملخص Cron Jobs

### المهام الأساسية

| المهمة | التكرار | الوقت المقترح | الأهمية |
|--------|---------|----------------|---------|
| إرسال البث المجدول | كل دقيقة | `* * * * *` | ⭐⭐⭐⭐⭐ |
| إنهاء الاشتراكات المنتهية | كل ساعة | `0 * * * *` | ⭐⭐⭐⭐⭐ |
| إرسال تذكيرات الاشتراك | يومياً | `0 9 * * *` | ⭐⭐⭐⭐ |
| تعيين العملاء غير المعينين | يومياً | `0 8 * * *` | ⭐⭐⭐ |
| تنظيف التسجيلات غير المكتملة | يومياً | `0 2 * * *` | ⭐⭐ |
| إعادة تعيين العملاء غير النشطين | كل ساعة | `0 * * * *` | ⭐⭐⭐ |

### مهام الإشعارات

| المهمة | التكرار | الوقت المقترح | الأهمية |
|--------|---------|----------------|---------|
| فحص Leads بدون متابعة | كل 30 دقيقة | `*/30 * * * *` | ⭐⭐⭐⭐ |
| فحص تذكيرات Leads | كل 15 دقيقة | `*/15 * * * *` | ⭐⭐⭐⭐ |
| فحص رسائل واتساب بانتظار الرد | كل ساعة | `0 * * * *` | ⭐⭐⭐ |
| فحص أداء الحملات الإعلانية | يومياً | `0 10 * * *` | ⭐⭐⭐⭐ |
| فحص تذكيرات المهام | كل 15 دقيقة | `*/15 * * * *` | ⭐⭐⭐ |
| فحص تذكيرات الصفقات | كل ساعة | `0 * * * *` | ⭐⭐⭐ |
| إرسال التقارير اليومية | يومياً | `0 9 * * *` | ⭐⭐⭐⭐ |
| إرسال التقارير الأسبوعية | أسبوعياً | `0 9 * * 1` | ⭐⭐⭐⭐ |
| إرسال إشعارات أفضل موظف | أسبوعياً | `0 10 * * 1` | ⭐⭐⭐ |
| فحص الاشتراكات المنتهية قريباً | يومياً | `0 9 * * *` | ⭐⭐⭐⭐⭐ |
| فحص الاشتراكات المنتهية | يومياً | `0 0 * * *` | ⭐⭐⭐⭐⭐ |

---

## ملاحظات مهمة

1. **الأمان:** تأكد من أن ملفات crontab محمية ولا يمكن الوصول إليها من قبل مستخدمين غير مصرح لهم.

2. **السجلات:** احتفظ بسجلات لجميع المهام لتسهيل استكشاف الأخطاء.

3. **الاختبار:** استخدم `--dry-run` دائماً قبل إضافة مهام جديدة إلى crontab.

4. **المراقبة:** راقب سجلات المهام بانتظام للتأكد من عملها بشكل صحيح.

5. **النسخ الاحتياطي:** احتفظ بنسخة احتياطية من ملف crontab:
   ```bash
   crontab -l > crontab_backup.txt
   ```

---

## الدعم

إذا واجهت أي مشاكل، راجع:
- سجلات المهام في `/var/log/crm/`
- سجلات Django في إعدادات المشروع
- سجلات Cron العامة في `/var/log/syslog`

---

---

## 📝 ملاحظات إضافية للإشعارات

### 1. تتبع الحقول المطلوبة

بعض الإشعارات تتطلب حقول إضافية في Models:

- **WhatsApp Waiting Response:** يتطلب تتبع `last_message_sent_at` في `Client` model
- **Campaign Budget Alert:** يتطلب تتبع `spent` في `Campaign` model
- **Deal Reminder:** يمكن إضافة `reminder_date` field في `Deal` model

### 2. تحسين الأداء

- استخدم `select_related()` و `prefetch_related()` في Queries
- فكر في استخدام Django Q2 أو Celery للمهام المتكررة
- راقب عدد الإشعارات المرسلة لتجنب تجاوز Firebase quota

### 3. الاختبار

قبل تفعيل Cron Jobs في الإنتاج:

```bash
# اختبار جميع الأوامر
python manage.py check_lead_no_follow_up --dry-run
python manage.py check_lead_reminders --dry-run
python manage.py check_whatsapp_waiting_response --dry-run
python manage.py check_campaign_performance --dry-run
python manage.py check_task_reminders --dry-run
python manage.py check_deal_reminders --dry-run
python manage.py send_daily_report --dry-run
python manage.py send_weekly_report --dry-run
python manage.py send_top_employee_notification --dry-run
python manage.py check_subscription_expiring --dry-run
python manage.py check_subscription_expired --dry-run
```

---

**آخر تحديث:** 2024
**الإصدار:** 2.0
