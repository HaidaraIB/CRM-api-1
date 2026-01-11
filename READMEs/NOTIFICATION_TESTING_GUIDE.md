# دليل اختبار الإشعارات - Notification Testing Guide

هذا الدليل الشامل يوضح كيفية اختبار جميع أنواع الإشعارات المتاحة في النظام.

---

## 📋 جدول المحتويات

1. [إعدادات ما قبل الاختبار](#إعدادات-ما-قبل-الاختبار)
2. [إشعارات العملاء المحتملين (Core Notifications)](#إشعارات-العملاء-المحتملين)
3. [إشعارات واتساب (WhatsApp Automation)](#إشعارات-واتساب)
4. [إشعارات الحملات الإعلانية (Ads Performance)](#إشعارات-الحملات-الإعلانية)
5. [إشعارات الفريق والمهام (Team & Tasks)](#إشعارات-الفريق-والمهام)
6. [إشعارات الصفقات (Deals)](#إشعارات-الصفقات)
7. [إشعارات التقارير (Reports & Insights)](#إشعارات-التقارير)
8. [إشعارات النظام والاشتراك (System & Subscription)](#إشعارات-النظام-والاشتراك)

---

## 🔧 إعدادات ما قبل الاختبار

### 1. التحقق من Firebase Configuration
```bash
# في Django shell
python manage.py shell
>>> from notifications.services import NotificationService
>>> NotificationService.initialize()
True
```

### 2. التحقق من FCM Token
```bash
# في Django shell
>>> from accounts.models import User
>>> user = User.objects.get(id=YOUR_USER_ID)
>>> print(user.fcm_token)  # يجب أن يكون موجوداً
>>> print(user.language)  # يجب أن يكون 'ar' أو 'en'
```

### 3. تفعيل الإشعارات في Flutter App
- افتح التطبيق
- اذهب إلى Settings → Notification Settings
- تأكد من تفعيل "Enable Notifications"
- فعّل النوع المحدد الذي تريد اختباره

---

## 👤 إشعارات العملاء المحتملين (Core Notifications)

### 1. 📥 New Lead (`new_lead`)

**الوصف:** إشعار عند إضافة عميل محتمل جديد من حملة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type new_lead --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App (Admin Panel):**
1. سجل دخول كـ Admin
2. اذهب إلى Leads/Clients
3. اضغط "Add New Lead"
4. املأ البيانات:
   - Name: "Test Lead"
   - Phone: "1234567890"
   - Campaign: اختر حملة موجودة
   - Company: اختر الشركة
5. احفظ Lead
6. **النتيجة:** Admin (owner) سيستقبل إشعار `new_lead`

**API Endpoint:**
```bash
POST /api/clients/
Headers: Authorization: Bearer TOKEN
Body: {
  "name": "Test Lead",
  "phone_number": "1234567890",
  "campaign": CAMPAIGN_ID,
  "company": COMPANY_ID,
  ...
}
```

---

### 2. 👤 Lead Assigned (`lead_assigned`)

**الوصف:** إشعار عند تعيين عميل محتمل لموظف

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_assigned --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App:**
1. سجل دخول كـ Admin
2. اذهب إلى Leads
3. اختر Lead موجود
4. اضغط "Assign" أو "Edit"
5. اختر موظف من قائمة "Assigned To"
6. احفظ التغييرات
7. **النتيجة:** الموظف المعين سيستقبل إشعار `lead_assigned`

**API Endpoint:**
```bash
PATCH /api/clients/{id}/
Headers: Authorization: Bearer TOKEN
Body: {
  "assigned_to": EMPLOYEE_USER_ID
}
```

**من Flutter App:**
1. افتح Lead Details
2. اضغط "Assign Lead"
3. اختر موظف
4. احفظ
5. **النتيجة:** الموظف المعين سيستقبل إشعار

---

### 3. 🔁 Lead Transferred (`lead_transferred`)

**الوصف:** إشعار عند نقل عميل محتمل من موظف لآخر

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_transferred --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App:**
1. سجل دخول كـ Admin
2. اذهب إلى Leads
3. اختر Lead معين لموظف (مثلاً: Employee A)
4. غيّر "Assigned To" إلى موظف آخر (Employee B)
5. احفظ التغييرات
6. **النتيجة:** 
   - Employee A (القديم) سيستقبل إشعار `lead_transferred`
   - Employee B (الجديد) سيستقبل إشعار `lead_assigned`

**API Endpoint:**
```bash
PATCH /api/clients/{id}/
Body: {
  "assigned_to": NEW_EMPLOYEE_USER_ID  # تغيير من موظف لآخر
}
```

---

### 4. 🔄 Lead Status Changed (`lead_status_changed`)

**الوصف:** إشعار عند تغيير حالة العميل المحتمل

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_status_changed --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App:**
1. سجل دخول كـ Admin أو Employee
2. اذهب إلى Leads
3. اختر Lead موجود
4. غيّر "Status" من قائمة الحالات
5. احفظ التغييرات
6. **النتيجة:** الموظف المعين للـ Lead سيستقبل إشعار `lead_status_changed`

**API Endpoint:**
```bash
PATCH /api/clients/{id}/
Body: {
  "status": NEW_STATUS_ID
}
```

**من Flutter App:**
1. افتح Lead Details
2. اضغط على Status
3. اختر حالة جديدة
4. احفظ
5. **النتيجة:** الموظف المعين سيستقبل إشعار

---

### 5. 🔄 Lead Updated (`lead_updated`)

**الوصف:** إشعار عند تحديث معلومات العميل المحتمل

**ملاحظة:** هذا الإشعار معطل حالياً في `crm/signals.py` (معلق) لتجنب الإشعارات المكررة.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_updated --user-id USER_ID
```

#### تفعيل الإشعار (Live):
إذا أردت تفعيله، قم بتعديل `crm/signals.py`:
```python
@receiver(post_save, sender=Client)
def notify_lead_updated(sender, instance, created, **kwargs):
    if created:
        return
    
    if instance.assigned_to:
        NotificationService.send_notification(
            user=instance.assigned_to,
            notification_type=NotificationType.LEAD_UPDATED,
            data={
                'lead_id': instance.id,
                'lead_name': instance.name,
            }
        )
```

**طريقة الاختبار (Live):**
1. عدّل أي حقل في Lead (Name, Phone, Budget, etc.)
2. احفظ التغييرات
3. **النتيجة:** الموظف المعين سيستقبل إشعار `lead_updated`

---

### 6. ⏱️ Lead No Follow Up (`lead_no_follow_up`)

**الوصف:** إشعار عند عدم متابعة عميل محتمل لمدة معينة

**ملاحظة:** يتطلب إعداد Background Task أو Cron Job للتحقق من Leads بدون متابعة.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_no_follow_up --user-id USER_ID
```

#### طريقة الاختبار (Live):
**يتطلب إعداد Task:**
```python
# في crm/tasks.py أو management command
from django.utils import timezone
from datetime import timedelta
from crm.models import Client
from notifications.services import NotificationService

# Find leads without follow-up for 30 minutes
threshold = timezone.now() - timedelta(minutes=30)
leads = Client.objects.filter(
    last_contacted_at__lt=threshold,
    assigned_to__isnull=False
)

for lead in leads:
    NotificationService.send_notification(
        user=lead.assigned_to,
        notification_type=NotificationType.LEAD_NO_FOLLOW_UP,
        data={
            'lead_id': lead.id,
            'lead_name': lead.name,
            'minutes': 30,
        }
    )
```

**جدولة Task:**
```bash
# في Django Q2 أو Celery
schedule('check_no_follow_up', func=check_no_follow_up_leads, schedule_type=Schedule.MINUTES, minutes=30)
```

---

### 7. 🔁 Lead Reengaged (`lead_reengaged`)

**الوصف:** إشعار عند عودة عميل محتمل سابق للتفاعل

**ملاحظة:** يتطلب منطق للتحقق من إعادة التفاعل (مثلاً: رسالة واتساب جديدة بعد فترة).

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_reengaged --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من WhatsApp Integration:**
1. Lead كان "Cold" أو "Inactive"
2. استقبل رسالة واتساب جديدة من هذا Lead
3. **النتيجة:** الموظف المعين سيستقبل إشعار `lead_reengaged`

**API Endpoint (Manual):**
```bash
POST /api/notifications/send/
Headers: Authorization: Bearer ADMIN_TOKEN
Body: {
  "type": "lead_reengaged",
  "user_id": EMPLOYEE_ID,
  "data": {
    "lead_id": LEAD_ID,
    "lead_name": "Lead Name"
  }
}
```

---

### 8. ❌ Lead Contact Failed (`lead_contact_failed`)

**الوصف:** إشعار عند فشل التواصل بعد عدة محاولات

**ملاحظة:** يتطلب تتبع محاولات الاتصال.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_contact_failed --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من ClientTask أو Call Log:**
1. سجل 3 محاولات اتصال فاشلة لـ Lead
2. عند المحاولة الثالثة الفاشلة:
```python
# في crm/views.py أو signals
if failed_attempts >= 3:
    NotificationService.send_notification(
        user=lead.assigned_to,
        notification_type=NotificationType.LEAD_CONTACT_FAILED,
        data={
            'lead_id': lead.id,
            'lead_name': lead.name,
            'attempts': failed_attempts,
        }
    )
```

---

### 9. ⏰ Lead Reminder (`lead_reminder`)

**الوصف:** تذكير بموعد متابعة عميل محتمل

**ملاحظة:** يتطلب إعداد Reminder في ClientTask أو ClientEvent.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type lead_reminder --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من ClientTask:
1. أنشئ ClientTask مع due_date
2. عند اقتراب due_date (مثلاً: 30 دقيقة قبل):
```python
# في Background Task
from django.utils import timezone
from datetime import timedelta

tasks = ClientTask.objects.filter(
    due_date__lte=timezone.now() + timedelta(minutes=30),
    due_date__gt=timezone.now(),
    completed=False
)

for task in tasks:
    NotificationService.send_notification(
        user=task.client.assigned_to,
        notification_type=NotificationType.LEAD_REMINDER,
        data={
            'lead_id': task.client.id,
            'lead_name': task.client.name,
            'reminder_time': task.due_date.isoformat(),
        }
    )
```

---

## 💬 إشعارات واتساب (WhatsApp Automation)

### 10. 📨 WhatsApp Message Received (`whatsapp_message_received`)

**الوصف:** إشعار عند استقبال رسالة واتساب جديدة من عميل محتمل

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type whatsapp_message_received --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من WhatsApp Webhook:**
1. أرسل رسالة واتساب من رقم العميل
2. WhatsApp Webhook يستقبل الرسالة
3. في `integrations/whatsapp_webhook.py`:
```python
# عند استقبال رسالة
NotificationService.send_notification(
    user=lead.assigned_to,
    notification_type=NotificationType.WHATSAPP_MESSAGE_RECEIVED,
    data={
        'lead_id': lead.id,
        'lead_name': lead.name,
        'message': message_text,
    }
)
```

**API Endpoint (Manual):**
```bash
POST /api/notifications/send/
Body: {
  "type": "whatsapp_message_received",
  "user_id": EMPLOYEE_ID,
  "data": {
    "lead_id": LEAD_ID,
    "lead_name": "Lead Name",
    "message": "Hello"
  }
}
```

---

### 11. 📤 WhatsApp Template Sent (`whatsapp_template_sent`)

**الوصف:** إشعار عند إرسال قالب واتساب بنجاح

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type whatsapp_template_sent --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من WhatsApp Integration:**
1. أرسل قالب واتساب من React App أو Flutter App
2. عند نجاح الإرسال:
```python
# في integrations/whatsapp_utils.py أو views.py
NotificationService.send_notification(
    user=request.user,
    notification_type=NotificationType.WHATSAPP_TEMPLATE_SENT,
    data={
        'lead_id': lead.id,
        'lead_name': lead.name,
        'template_name': template_name,
    }
)
```

**API Endpoint:**
```bash
POST /api/integrations/whatsapp/send-template/
Body: {
  "lead_id": LEAD_ID,
  "template_name": "welcome"
}
```

---

### 12. ⚠️ WhatsApp Send Failed (`whatsapp_send_failed`)

**الوصف:** إشعار عند فشل إرسال قالب واتساب

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type whatsapp_send_failed --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من WhatsApp Integration:**
1. حاول إرسال قالب واتساب
2. عند فشل الإرسال:
```python
# في integrations/whatsapp_utils.py
try:
    send_whatsapp_template(...)
except Exception as e:
    NotificationService.send_notification(
        user=request.user,
        notification_type=NotificationType.WHATSAPP_SEND_FAILED,
        data={
            'lead_id': lead.id,
            'lead_name': lead.name,
            'error': str(e),
        }
    )
```

---

### 13. ⏳ WhatsApp Waiting Response (`whatsapp_waiting_response`)

**الوصف:** إشعار عند عدم وجود رد من العميل المحتمل منذ فترة

**ملاحظة:** يتطلب Background Task للتحقق من آخر رسالة مرسلة.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type whatsapp_waiting_response --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task:**
```python
# في crm/tasks.py
from django.utils import timezone
from datetime import timedelta

# Find leads with last sent message > 24 hours ago
threshold = timezone.now() - timedelta(hours=24)
leads = Client.objects.filter(
    last_message_sent_at__lt=threshold,
    assigned_to__isnull=False
)

for lead in leads:
    NotificationService.send_notification(
        user=lead.assigned_to,
        notification_type=NotificationType.WHATSAPP_WAITING_RESPONSE,
        data={
            'lead_id': lead.id,
            'lead_name': lead.name,
            'hours': 24,
        }
    )
```

---

## 📢 إشعارات الحملات الإعلانية (Ads Performance)

### 14. 📊 Campaign Performance (`campaign_performance`)

**الوصف:** إشعار عند تحقيق الحملة لعدد معين من العملاء المحتملين

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type campaign_performance --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task أو Signal:**
```python
# في crm/signals.py أو tasks.py
# عند إنشاء Lead جديد من حملة
if instance.campaign:
    campaign_leads_count = Client.objects.filter(
        campaign=instance.campaign
    ).count()
    
    if campaign_leads_count == 100:  # مثال: عند الوصول لـ 100 lead
        NotificationService.send_notification(
            user=instance.company.owner,
            notification_type=NotificationType.CAMPAIGN_PERFORMANCE,
            data={
                'campaign_id': instance.campaign.id,
                'campaign_name': instance.campaign.name,
                'leads_count': campaign_leads_count,
            }
        )
```

**API Endpoint (Manual):**
```bash
POST /api/notifications/send/
Body: {
  "type": "campaign_performance",
  "user_id": ADMIN_ID,
  "data": {
    "campaign_id": CAMPAIGN_ID,
    "campaign_name": "Facebook Campaign",
    "leads_count": 100
  }
}
```

---

### 15. ⚠️ Campaign Low Performance (`campaign_low_performance`)

**الوصف:** إشعار عند انخفاض أداء الحملة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type campaign_low_performance --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task:**
```python
# في crm/tasks.py
from django.utils import timezone
from datetime import timedelta

campaigns = Campaign.objects.filter(is_active=True)
for campaign in campaigns:
    # Count leads from today
    today = timezone.now().date()
    today_leads = Client.objects.filter(
        campaign=campaign,
        created_at__date=today
    ).count()
    
    # Compare with average
    avg_daily_leads = campaign.avg_daily_leads  # يجب حسابها مسبقاً
    
    if today_leads < avg_daily_leads * 0.5:  # أقل من 50% من المتوسط
        NotificationService.send_notification(
            user=campaign.company.owner,
            notification_type=NotificationType.CAMPAIGN_LOW_PERFORMANCE,
            data={
                'campaign_id': campaign.id,
                'campaign_name': campaign.name,
                'today_leads': today_leads,
            }
        )
```

---

### 16. ⛔ Campaign Stopped (`campaign_stopped`)

**الوصف:** إشعار عند إيقاف الحملة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type campaign_stopped --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App:**
1. اذهب إلى Campaigns
2. اختر حملة
3. اضغط "Stop Campaign" أو "Deactivate"
4. في `crm/views.py`:
```python
@action(detail=True, methods=['post'])
def stop_campaign(self, request, pk=None):
    campaign = self.get_object()
    campaign.is_active = False
    campaign.save()
    
    NotificationService.send_notification(
        user=campaign.company.owner,
        notification_type=NotificationType.CAMPAIGN_STOPPED,
        data={
            'campaign_id': campaign.id,
            'campaign_name': campaign.name,
            'reason': 'Budget exhausted',  # أو أي سبب آخر
        }
    )
```

**API Endpoint:**
```bash
PATCH /api/campaigns/{id}/
Body: {
  "is_active": false
}
```

---

### 17. 💰 Campaign Budget Alert (`campaign_budget_alert`)

**الوصف:** إشعار عند انخفاض ميزانية الحملة عن نسبة معينة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type campaign_budget_alert --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task:**
```python
# في crm/tasks.py
campaigns = Campaign.objects.filter(is_active=True)
for campaign in campaigns:
    if campaign.budget and campaign.spent:
        remaining_percent = ((campaign.budget - campaign.spent) / campaign.budget) * 100
        
        if remaining_percent < 20:  # أقل من 20%
            NotificationService.send_notification(
                user=campaign.company.owner,
                notification_type=NotificationType.CAMPAIGN_BUDGET_ALERT,
                data={
                    'campaign_id': campaign.id,
                    'campaign_name': campaign.name,
                    'remaining_percent': remaining_percent,
                }
            )
```

---

## 👥 إشعارات الفريق والمهام (Team & Tasks)

### 18. 📌 Task Created (`task_created`)

**الوصف:** إشعار عند إنشاء مهمة جديدة

**ملاحظة:** يتطلب إضافة Signal في `crm/signals.py`.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type task_created --user-id USER_ID
```

#### تفعيل الإشعار (Live):
**أضف في `crm/signals.py`:**
```python
@receiver(post_save, sender=Task)
def notify_task_created(sender, instance, created, **kwargs):
    if not created:
        return
    
    try:
        if instance.assigned_to:
            NotificationService.send_notification(
                user=instance.assigned_to,
                notification_type=NotificationType.TASK_CREATED,
                data={
                    'task_id': instance.id,
                    'task_title': instance.title,
                    'due_date': instance.due_date.isoformat() if instance.due_date else None,
                }
            )
    except Exception as e:
        logger.error(f"Error sending task created notification: {e}")
```

**طريقة الاختبار (Live):**
1. من React App أو Flutter App
2. أنشئ Task جديد
3. **النتيجة:** الموظف المعين سيستقبل إشعار

**API Endpoint:**
```bash
POST /api/tasks/
Body: {
  "title": "Follow up with client",
  "assigned_to": EMPLOYEE_ID,
  "due_date": "2024-01-15T10:00:00Z"
}
```

---

### 19. ⏰ Task Reminder (`task_reminder`)

**الوصف:** تذكير بموعد مهمة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type task_reminder --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task:**
```python
# في crm/tasks.py
from django.utils import timezone
from datetime import timedelta

tasks = Task.objects.filter(
    due_date__lte=timezone.now() + timedelta(minutes=30),
    due_date__gt=timezone.now(),
    completed=False,
    assigned_to__isnull=False
)

for task in tasks:
    minutes_remaining = (task.due_date - timezone.now()).total_seconds() / 60
    
    NotificationService.send_notification(
        user=task.assigned_to,
        notification_type=NotificationType.TASK_REMINDER,
        data={
            'task_id': task.id,
            'task_title': task.title,
            'minutes_remaining': int(minutes_remaining),
        }
    )
```

---

### 20. ✅ Task Completed (`task_completed`)

**الوصف:** إشعار عند إكمال مهمة

**ملاحظة:** يتطلب إضافة Signal.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type task_completed --user-id USER_ID
```

#### تفعيل الإشعار (Live):
**أضف في `crm/signals.py`:**
```python
@receiver(pre_save, sender=Task)
def notify_task_completed(sender, instance, **kwargs):
    if instance.pk:
        try:
            old_instance = Task.objects.get(pk=instance.pk)
            if not old_instance.completed and instance.completed:
                # Task was just completed
                if instance.assigned_to:
                    NotificationService.send_notification(
                        user=instance.assigned_to,
                        notification_type=NotificationType.TASK_COMPLETED,
                        data={
                            'task_id': instance.id,
                            'task_title': instance.title,
                        }
                    )
        except Task.DoesNotExist:
            pass
```

**طريقة الاختبار (Live):**
1. من React App أو Flutter App
2. افتح Task
3. اضغط "Mark as Completed"
4. **النتيجة:** الموظف المعين سيستقبل إشعار

---

## 🤝 إشعارات الصفقات (Deals)

### 21. 💼 Deal Created (`deal_created`)

**الوصف:** إشعار عند إنشاء صفقة جديدة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type deal_created --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App:**
1. اذهب إلى Deals
2. اضغط "Add New Deal"
3. املأ البيانات:
   - Client: اختر عميل
   - Value: 50000
   - Employee: اختر موظف
4. احفظ
5. **النتيجة:** 
   - الموظف الذي أنشأ الصفقة سيستقبل إشعار
   - المالك (owner) سيستقبل إشعار

**API Endpoint:**
```bash
POST /api/deals/
Body: {
  "client": CLIENT_ID,
  "value": 50000,
  "employee": EMPLOYEE_ID,
  ...
}
```

**Signal موجود في:** `crm/signals.py` - `notify_deal_created`

---

### 22. 🔄 Deal Updated (`deal_updated`)

**الوصف:** إشعار عند تحديث صفقة

**ملاحظة:** يتطلب إضافة Signal.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type deal_updated --user-id USER_ID
```

#### تفعيل الإشعار (Live):
**أضف في `crm/signals.py`:**
```python
@receiver(post_save, sender=Deal)
def notify_deal_updated(sender, instance, created, **kwargs):
    if created:
        return  # Already handled by notify_deal_created
    
    try:
        if instance.employee:
            NotificationService.send_notification(
                user=instance.employee,
                notification_type=NotificationType.DEAL_UPDATED,
                data={
                    'deal_id': instance.id,
                    'deal_title': f'{instance.client.name} - {instance.value or 0}',
                }
            )
    except Exception as e:
        logger.error(f"Error sending deal updated notification: {e}")
```

**طريقة الاختبار (Live):**
1. عدّل أي حقل في Deal
2. احفظ
3. **النتيجة:** الموظف سيستقبل إشعار

---

### 23. 🎉 Deal Closed (`deal_closed`)

**الوصف:** إشعار عند إغلاق صفقة (Won)

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type deal_closed --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من React App:**
1. اذهب إلى Deals
2. اختر Deal
3. غيّر Stage إلى "Won"
4. احفظ
5. **النتيجة:** الموظف سيستقبل إشعار `deal_closed`

**API Endpoint:**
```bash
PATCH /api/deals/{id}/
Body: {
  "stage": "won"
}
```

**Signal موجود في:** `crm/signals.py` - `notify_deal_closed`

---

### 24. ⏰ Deal Reminder (`deal_reminder`)

**الوصف:** تذكير بموعد متابعة صفقة

**ملاحظة:** يتطلب إضافة Reminder logic.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type deal_reminder --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task:**
```python
# في crm/tasks.py
from django.utils import timezone
from datetime import timedelta

deals = Deal.objects.filter(
    reminder_date__lte=timezone.now() + timedelta(hours=1),
    reminder_date__gt=timezone.now(),
    stage__in=['in_progress', 'on_hold'],
    employee__isnull=False
)

for deal in deals:
    NotificationService.send_notification(
        user=deal.employee,
        notification_type=NotificationType.DEAL_REMINDER,
        data={
            'deal_id': deal.id,
            'deal_title': f'{deal.client.name} - {deal.value or 0}',
        }
    )
```

---

## 📈 إشعارات التقارير (Reports & Insights)

### 25. 📊 Daily Report (`daily_report`)

**الوصف:** تقرير يومي للأداء

**ملاحظة:** يتطلب Background Task يومي.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type daily_report --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task (Daily at 9 AM):**
```python
# في crm/tasks.py أو management command
from django.utils import timezone
from datetime import date

today = date.today()
leads_count = Client.objects.filter(
    company=company,
    created_at__date=today
).count()

deals_count = Deal.objects.filter(
    company=company,
    created_at__date=today,
    stage='won'
).count()

# Send to company owner
NotificationService.send_notification(
    user=company.owner,
    notification_type=NotificationType.DAILY_REPORT,
    data={
        'date': today.isoformat(),
        'leads_count': leads_count,
        'deals_count': deals_count,
    }
)
```

**جدولة:**
```bash
# في Django Q2
schedule('daily_report', func=send_daily_report, schedule_type=Schedule.DAILY, time='09:00')
```

---

### 26. 📅 Weekly Report (`weekly_report`)

**الوصف:** تقرير أسبوعي للأداء

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type weekly_report --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task (Weekly on Monday):**
```python
# في crm/tasks.py
from django.utils import timezone
from datetime import timedelta

week_start = timezone.now() - timedelta(days=7)
leads_count = Client.objects.filter(
    company=company,
    created_at__gte=week_start
).count()

deals_count = Deal.objects.filter(
    company=company,
    created_at__gte=week_start,
    stage='won'
).count()

NotificationService.send_notification(
    user=company.owner,
    notification_type=NotificationType.WEEKLY_REPORT,
    data={
        'week': week_start.strftime('%Y-W%W'),
        'leads_count': leads_count,
        'deals_count': deals_count,
    }
)
```

---

### 27. 🏆 Top Employee (`top_employee`)

**الوصف:** إشعار عن أفضل موظف مبيعات للأسبوع

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type top_employee --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task (Weekly):**
```python
# في crm/tasks.py
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count

week_start = timezone.now() - timedelta(days=7)

# Get top employee by deals count
top_employee = User.objects.filter(
    company=company,
    role='employee'
).annotate(
    deals_count=Count('deals', filter=Q(deals__created_at__gte=week_start, deals__stage='won'))
).order_by('-deals_count').first()

if top_employee and top_employee.deals_count > 0:
    # Notify company owner
    NotificationService.send_notification(
        user=company.owner,
        notification_type=NotificationType.TOP_EMPLOYEE,
        data={
            'employee_id': top_employee.id,
            'employee_name': top_employee.username,
            'deals_count': top_employee.deals_count,
        }
    )
    
    # Notify the top employee
    NotificationService.send_notification(
        user=top_employee,
        notification_type=NotificationType.TOP_EMPLOYEE,
        data={
            'employee_id': top_employee.id,
            'employee_name': top_employee.username,
            'deals_count': top_employee.deals_count,
        }
    )
```

---

## 🧾 إشعارات النظام والاشتراك (System & Subscription)

### 28. 🔐 Login From New Device (`login_from_new_device`)

**الوصف:** إشعار عند تسجيل دخول من جهاز جديد

**ملاحظة:** يتطلب تتبع الأجهزة.

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type login_from_new_device --user-id USER_ID
```

#### تفعيل الإشعار (Live):
**في `accounts/views.py` - `CustomTokenObtainPairView`:**
```python
# بعد تسجيل الدخول الناجح
device_id = request.META.get('HTTP_DEVICE_ID')
if device_id:
    # Check if this is a new device
    if not UserDevice.objects.filter(user=user, device_id=device_id).exists():
        # New device
        NotificationService.send_notification(
            user=user,
            notification_type=NotificationType.LOGIN_FROM_NEW_DEVICE,
            data={
                'device': request.META.get('HTTP_USER_AGENT', 'Unknown'),
                'location': get_location_from_ip(request.META.get('REMOTE_ADDR')),
                'ip': request.META.get('REMOTE_ADDR'),
            }
        )
        
        # Save device
        UserDevice.objects.create(user=user, device_id=device_id)
```

---

### 29. ⚙️ System Update (`system_update`)

**الوصف:** إشعار عند تحديث النظام أو إضافة ميزة جديدة

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type system_update --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من Admin Panel:**
```bash
POST /api/notifications/send/
Body: {
  "type": "system_update",
  "company_id": COMPANY_ID,  # لإرسال لجميع مستخدمي الشركة
  "data": {
    "version": "2.0.0",
    "feature": "نظام الإشعارات"
  }
}
```

**أو من Django Admin:**
```python
# في Django shell
from notifications.services import NotificationService
from companies.models import Company

company = Company.objects.get(id=COMPANY_ID)
NotificationService.send_notification_to_company(
    company=company,
    notification_type=NotificationType.SYSTEM_UPDATE,
    data={
        'version': '2.0.0',
        'feature': 'نظام الإشعارات',
    }
)
```

---

### 30. 💳 Subscription Expiring (`subscription_expiring`)

**الوصف:** إشعار عند اقتراب انتهاء الاشتراك

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type subscription_expiring --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task (Daily):**
```python
# في subscriptions/tasks.py
from django.utils import timezone
from datetime import timedelta
from subscriptions.models import Subscription

# Find subscriptions expiring in 3 days
expiry_date = timezone.now().date() + timedelta(days=3)
subscriptions = Subscription.objects.filter(
    status='active',
    end_date=expiry_date
)

for subscription in subscriptions:
    NotificationService.send_notification(
        user=subscription.company.owner,
        notification_type=NotificationType.SUBSCRIPTION_EXPIRING,
        data={
            'days_remaining': 3,
            'expiry_date': subscription.end_date.isoformat(),
        }
    )
```

---

### 31. ❌ Payment Failed (`payment_failed`)

**الوصف:** إشعار عند فشل عملية الدفع

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type payment_failed --user-id USER_ID
```

#### طريقة الاختبار (Live):
**من Payment Gateway Webhook:**
```python
# في subscriptions/views.py أو webhook handler
@api_view(['POST'])
def payment_webhook(request):
    # Process payment
    if payment_status == 'failed':
        NotificationService.send_notification(
            user=subscription.company.owner,
            notification_type=NotificationType.PAYMENT_FAILED,
            data={
                'payment_id': payment.id,
                'amount': payment.amount,
                'reason': payment.failure_reason,
            }
        )
```

---

### 32. ⛔ Subscription Expired (`subscription_expired`)

**الوصف:** إشعار عند انتهاء الاشتراك

#### طريقة الاختبار (Testing):
```bash
python manage.py test_notifications --type subscription_expired --user-id USER_ID
```

#### طريقة الاختبار (Live):
**Background Task (Daily):**
```python
# في subscriptions/tasks.py
from django.utils import timezone
from subscriptions.models import Subscription

# Find expired subscriptions
expired_subscriptions = Subscription.objects.filter(
    status='active',
    end_date__lt=timezone.now().date()
)

for subscription in expired_subscriptions:
    NotificationService.send_notification(
        user=subscription.company.owner,
        notification_type=NotificationType.SUBSCRIPTION_EXPIRED,
        data={
            'expiry_date': subscription.end_date.isoformat(),
        }
    )
    
    # Deactivate subscription
    subscription.status = 'expired'
    subscription.save()
```

---

## 🧪 اختبار جميع الأنواع دفعة واحدة

### اختبار جميع الإشعارات:
```bash
python manage.py test_notifications --all --user-id USER_ID
```

### اختبار نوع محدد:
```bash
python manage.py test_notifications --type TYPE_NAME --user-id USER_ID
```

### قائمة بجميع الأنواع:
```bash
python manage.py test_notifications
# سيظهر قائمة بجميع الأنواع المتاحة
```

---

## 📝 ملاحظات مهمة

### 1. التحقق من الإشعارات في Flutter App:
- افتح التطبيق
- اضغط على زر الإشعارات في AppBar
- تحقق من ظهور الإشعار في قائمة الإشعارات
- اضغط على الإشعار للتحقق من التنقل الصحيح

### 2. التحقق من اللغة:
- تأكد من أن `user.language` في قاعدة البيانات = اللغة المطلوبة
- الإشعارات يجب أن تظهر باللغة الصحيحة

### 3. التحقق من FCM Token:
- تأكد من أن المستخدم لديه `fcm_token` في قاعدة البيانات
- إذا كان `null`، سجل دخول من Flutter App مرة أخرى

### 4. Background Tasks:
- بعض الإشعارات تتطلب Background Tasks (Cron Jobs)
- استخدم Django Q2 أو Celery لجدولة المهام

### 5. Logs:
- تحقق من Django logs للأخطاء
- تحقق من Flutter app logs
- تحقق من Firebase Console

---

## 🔍 استكشاف الأخطاء

### الإشعارات لا تظهر:
1. تحقق من Firebase initialization
2. تحقق من FCM token
3. تحقق من إعدادات الإشعارات في التطبيق
4. تحقق من permissions في Android/iOS

### اللغة خاطئة:
1. تحقق من `user.language` في قاعدة البيانات
2. تحقق من `notifications/translations.py`
3. أعد إرسال FCM token مع اللغة الصحيحة

### Signal لا يعمل:
1. تحقق من أن `crm/apps.py` يحتوي على `ready()` method
2. تحقق من أن `crm` في `INSTALLED_APPS`
3. أعد تشغيل Django server

---

## 📞 الدعم

للمساعدة:
1. راجع `NOTIFICATIONS_PRODUCTION_CHECKLIST.md`
2. تحقق من Django logs
3. تحقق من Flutter app logs
4. راجع هذا الدليل

---

**آخر تحديث:** 2024
**الإصدار:** 1.0.0
