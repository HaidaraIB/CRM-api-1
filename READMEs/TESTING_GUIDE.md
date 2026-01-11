# دليل اختبار الإشعارات - Notification Testing Guide

## الطرق المتاحة لاختبار الإشعارات

### 1. استخدام Management Command (الطريقة الموصى بها)

#### اختبار نوع محدد:
```bash
python manage.py test_notifications --type new_lead
python manage.py test_notifications --type whatsapp_message_received
python manage.py test_notifications --type deal_created
```

#### اختبار جميع الأنواع:
```bash
python manage.py test_notifications --all
```

#### اختبار لمستخدم محدد:
```bash
python manage.py test_notifications --user-id 1 --type new_lead
python manage.py test_notifications --user-id 1 --all
```

#### عرض القائمة التفاعلية:
```bash
python manage.py test_notifications
```

### 2. استخدام Django Shell

```bash
python manage.py shell
```

```python
from accounts.models import User
from notifications.services import NotificationService
from notifications.models import NotificationType

# الحصول على مستخدم
user = User.objects.get(id=1)  # استبدل 1 بـ ID المستخدم

# إرسال إشعار محدد
NotificationService.send_notification(
    user=user,
    notification_type=NotificationType.NEW_LEAD,
    title='عميل محتمل جديد',
    body='تم إضافة عميل محتمل جديد من حملة فيسبوك',
    data={
        'lead_id': 123,
        'lead_name': 'أحمد محمد',
        'campaign_name': 'حملة فيسبوك',
    }
)

# اختبار جميع الأنواع
for notification_type, display_name in NotificationType.choices:
    NotificationService.send_notification(
        user=user,
        notification_type=notification_type,
        title=f'Test: {display_name}',
        body=f'This is a test notification for {display_name}',
        data={'test': True}
    )
    print(f'Sent: {display_name}')
```

### 3. استخدام API Endpoint

#### إرسال إشعار عبر API (للمدراء فقط):

```bash
POST /api/notifications/send/
Authorization: Bearer <your-token>
Content-Type: application/json

{
  "type": "new_lead",
  "title": "عميل محتمل جديد",
  "body": "تم إضافة عميل محتمل جديد",
  "user_id": 1,
  "data": {
    "lead_id": 123,
    "lead_name": "أحمد محمد"
  }
}
```

#### مثال باستخدام curl:
```bash
curl -X POST http://localhost:8000/api/notifications/send/ \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "new_lead",
    "title": "عميل محتمل جديد",
    "body": "تم إضافة عميل محتمل جديد",
    "user_id": 1,
    "data": {
      "lead_id": 123,
      "lead_name": "أحمد محمد"
    }
  }'
```

### 4. اختبار من Flutter App

يمكنك استخدام زر "Test Notification" في صفحة إعدادات الإشعارات (`NotificationSettingsScreen`).

## أنواع الإشعارات المتاحة للاختبار

### إشعارات العملاء المحتملين
- `new_lead` - عميل محتمل جديد
- `lead_no_follow_up` - بدون متابعة
- `lead_reengaged` - إعادة تفاعل
- `lead_contact_failed` - فشل التواصل
- `lead_status_changed` - تغيير الحالة
- `lead_assigned` - تعيين عميل
- `lead_transferred` - نقل عميل
- `lead_updated` - تحديث عميل
- `lead_reminder` - تذكير عميل

### إشعارات واتساب
- `whatsapp_message_received` - رسالة واردة
- `whatsapp_template_sent` - إرسال قالب
- `whatsapp_send_failed` - فشل الإرسال
- `whatsapp_waiting_response` - بانتظار الرد

### إشعارات الحملات
- `campaign_performance` - أداء الحملة
- `campaign_low_performance` - انخفاض الأداء
- `campaign_stopped` - إيقاف حملة
- `campaign_budget_alert` - تنبيه الميزانية

### إشعارات المهام
- `task_created` - مهمة جديدة
- `task_reminder` - تذكير مهمة
- `task_completed` - مهمة مكتملة

### إشعارات الصفقات
- `deal_created` - صفقة جديدة
- `deal_updated` - تحديث صفقة
- `deal_closed` - إغلاق صفقة
- `deal_reminder` - تذكير صفقة

### إشعارات التقارير
- `daily_report` - تقرير يومي
- `weekly_report` - تقرير أسبوعي
- `top_employee` - أفضل موظف

### إشعارات النظام
- `login_from_new_device` - تسجيل دخول جديد
- `system_update` - تحديث النظام
- `subscription_expiring` - تنبيه الاشتراك
- `payment_failed` - فشل الدفع
- `subscription_expired` - انتهاء الاشتراك

### إشعارات عامة
- `general` - إشعار عام

## التحقق من النتائج

### 1. في قاعدة البيانات:
```python
from notifications.models import Notification

# جلب جميع الإشعارات
notifications = Notification.objects.all()

# جلب إشعارات مستخدم محدد
user_notifications = Notification.objects.filter(user_id=1)

# جلب إشعارات غير مقروءة
unread = Notification.objects.filter(user_id=1, read=False)
```

### 2. في Flutter App:
- افتح صفحة الإشعارات (إن وجدت)
- تحقق من وصول الإشعار على الجهاز
- تحقق من التنقل عند النقر على الإشعار

### 3. في Firebase Console:
- اذهب إلى Firebase Console → Cloud Messaging
- تحقق من سجل الإشعارات المرسلة

## ملاحظات مهمة

1. **FCM Token**: تأكد من أن المستخدم لديه FCM token صحيح
2. **Firebase Credentials**: تأكد من إعداد Firebase credentials بشكل صحيح
3. **الإشعارات المحلية**: حتى لو فشل إرسال FCM، الإشعارات تُحفظ في قاعدة البيانات
4. **الاختبار على جهاز حقيقي**: للاختبار الكامل، استخدم جهاز Android/iOS حقيقي

## استكشاف الأخطاء

### الإشعار لا يصل:
1. تحقق من FCM token في قاعدة البيانات
2. تحقق من Firebase credentials
3. تحقق من logs في Django
4. تحقق من Firebase Console

### خطأ في الإرسال:
1. تحقق من صحة نوع الإشعار
2. تحقق من صحة بيانات المستخدم
3. تحقق من logs في Django

### الإشعار يُحفظ لكن لا يُرسل:
- هذا طبيعي إذا لم يكن هناك FCM token أو Firebase credentials
- الإشعارات تُحفظ دائماً في قاعدة البيانات
