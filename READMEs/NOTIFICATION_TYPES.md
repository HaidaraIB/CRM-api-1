# أنواع الإشعارات - Notification Types

## قائمة كاملة بأنواع الإشعارات المدعومة

### إشعارات العملاء المحتملين (Core Notifications)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| عميل محتمل جديد | `new_lead` | عند إضافة عميل جديد من حملة |
| بدون متابعة | `lead_no_follow_up` | عميل لم يتم التواصل معه منذ فترة |
| إعادة تفاعل | `lead_reengaged` | عميل سابق عاد وتفاعل |
| فشل التواصل | `lead_contact_failed` | لم يتم الرد بعد محاولات اتصال |
| تغيير الحالة | `lead_status_changed` | تم تغيير حالة العميل |
| تعيين عميل | `lead_assigned` | تم تعيين عميل لموظف |
| نقل عميل | `lead_transferred` | تم نقل عميل بين موظفين |
| تحديث عميل | `lead_updated` | تم تحديث معلومات العميل |
| تذكير عميل | `lead_reminder` | تذكير بموعد متابعة |

### إشعارات واتساب (WhatsApp Automation)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| رسالة واردة | `whatsapp_message_received` | رسالة جديدة من عميل |
| إرسال قالب | `whatsapp_template_sent` | تم إرسال رسالة ترحيب |
| فشل الإرسال | `whatsapp_send_failed` | فشل إرسال قالب واتساب |
| بانتظار الرد | `whatsapp_waiting_response` | لا يوجد رد منذ فترة |

### إشعارات الحملات (Ads Performance)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| أداء الحملة | `campaign_performance` | الحملة حققت عدد معين |
| انخفاض الأداء | `campaign_low_performance` | انخفاض عدد العملاء اليوم |
| إيقاف حملة | `campaign_stopped` | تم إيقاف الحملة |
| تنبيه الميزانية | `campaign_budget_alert` | الميزانية المتبقية قليلة |

### إشعارات المهام (Team & Tasks)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| مهمة جديدة | `task_created` | تم إنشاء مهمة متابعة |
| تذكير مهمة | `task_reminder` | تبقى وقت على موعد المتابعة |
| مهمة مكتملة | `task_completed` | تم إكمال مهمة |

### إشعارات الصفقات (Deals)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| صفقة جديدة | `deal_created` | تم إنشاء صفقة |
| تحديث صفقة | `deal_updated` | تم تحديث صفقة |
| إغلاق صفقة | `deal_closed` | تم إغلاق صفقة |
| تذكير صفقة | `deal_reminder` | تذكير بموعد صفقة |

### إشعارات التقارير (Reports & Insights)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| تقرير يومي | `daily_report` | تقرير الأداء اليومي |
| تقرير أسبوعي | `weekly_report` | تقرير الأداء الأسبوعي |
| أفضل موظف | `top_employee` | أفضل موظف مبيعات |

### إشعارات النظام (System & Subscription)

| النوع | المفتاح | الوصف |
|------|---------|-------|
| تسجيل دخول جديد | `login_from_new_device` | تسجيل دخول من جهاز جديد |
| تحديث النظام | `system_update` | تم إضافة ميزة جديدة |
| تنبيه الاشتراك | `subscription_expiring` | الاشتراك ينتهي قريباً |
| فشل الدفع | `payment_failed` | فشل عملية الدفع |
| انتهاء الاشتراك | `subscription_expired` | انتهى الاشتراك |

### إشعارات عامة

| النوع | المفتاح | الوصف |
|------|---------|-------|
| إشعار عام | `general` | إشعار عام |

## الاستخدام في الكود

```python
from notifications.models import NotificationType
from notifications.services import NotificationService

# إرسال إشعار
NotificationService.send_notification(
    user=user,
    notification_type=NotificationType.NEW_LEAD,
    title='عميل محتمل جديد',
    body='تم إضافة عميل محتمل جديد',
    data={'lead_id': 123},
)
```
