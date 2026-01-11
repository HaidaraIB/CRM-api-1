# نظام الإشعارات - Notifications System

## نظرة عامة

نظام إشعارات كامل لإرسال Push Notifications عبر Firebase Cloud Messaging (FCM) مع دعم كامل لجميع أنواع الإشعارات المطلوبة.

## المكونات

### 1. Models (`notifications/models.py`)
- **Notification**: Model لحفظ جميع الإشعارات المرسلة
- **NotificationType**: Enum لجميع أنواع الإشعارات (40+ نوع)

### 2. Services (`notifications/services.py`)
- **NotificationService**: Service لإرسال الإشعارات عبر FCM
  - `send_notification()`: إرسال إشعار لمستخدم واحد
  - `send_notification_to_multiple()`: إرسال إشعار لعدة مستخدمين
  - `send_notification_to_company()`: إرسال إشعار لجميع مستخدمي شركة

### 3. Views (`notifications/views.py`)
- **NotificationViewSet**: ViewSet لإدارة الإشعارات
  - `GET /api/notifications/`: جلب جميع الإشعارات
  - `GET /api/notifications/{id}/`: جلب إشعار محدد
  - `POST /api/notifications/{id}/mark_read/`: تحديد إشعار كمقروء
  - `POST /api/notifications/mark_all_read/`: تحديد جميع الإشعارات كمقروءة
  - `GET /api/notifications/unread_count/`: جلب عدد الإشعارات غير المقروءة
  - `DELETE /api/notifications/delete_all_read/`: حذف جميع الإشعارات المقروءة
- **send_notification**: Endpoint لإرسال إشعار يدوياً (للمدراء فقط)

### 4. Signals (`crm/signals.py`)
إشعارات تلقائية عند:
- إنشاء عميل جديد → إشعار للموظف المعين + المالك (إذا من حملة)
- تغيير حالة العميل → إشعار للموظف المعين
- تعيين عميل → إشعار للموظف الجديد
- نقل عميل → إشعار للموظف القديم والجديد
- إنشاء صفقة → إشعار للموظف والمالك
- إغلاق صفقة → إشعار للموظف

## الإعداد

### 1. تثبيت Firebase Admin SDK

```bash
pip install firebase-admin
```

### 2. إعداد Firebase Credentials

#### الطريقة 1: ملف Credentials (موصى به للتطوير)
1. احصل على `service-account-key.json` من Firebase Console
2. احفظه في مكان آمن (مثلاً: `CRM-api-1/firebase-credentials.json`)
3. أضف إلى `.env`:
```env
FIREBASE_CREDENTIALS_PATH=/path/to/firebase-credentials.json
```

#### الطريقة 2: Environment Variable (موصى به للإنتاج)
```env
GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-credentials.json
```

### 3. إضافة FCM Token إلى User

تم إضافة `fcm_token` field إلى User model. عند تسجيل الدخول من التطبيق، يتم إرسال FCM token تلقائياً.

### 4. إنشاء Migrations

```bash
python manage.py makemigrations accounts notifications
python manage.py migrate
```

## الاستخدام

### إرسال إشعار يدوياً

```python
from notifications.services import NotificationService
from notifications.models import NotificationType

# إرسال إشعار لمستخدم واحد
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

# إرسال إشعار لجميع مستخدمي شركة
NotificationService.send_notification_to_company(
    company=company,
    notification_type=NotificationType.SYSTEM_UPDATE,
    title='تحديث النظام',
    body='تم إضافة ميزة جديدة',
    roles=['admin', 'employee'],  # اختياري
)
```

### إرسال إشعار عبر API

```bash
POST /api/notifications/send/
Authorization: Bearer <token>
Content-Type: application/json

{
  "type": "new_lead",
  "title": "عميل محتمل جديد",
  "body": "تم إضافة عميل محتمل جديد",
  "user_id": 1,  # أو company_id
  "data": {
    "lead_id": 123,
    "lead_name": "أحمد محمد"
  }
}
```

## أنواع الإشعارات المدعومة

### إشعارات العملاء المحتملين
- `new_lead`: عميل محتمل جديد
- `lead_no_follow_up`: بدون متابعة
- `lead_reengaged`: إعادة تفاعل
- `lead_contact_failed`: فشل التواصل
- `lead_status_changed`: تغيير الحالة
- `lead_assigned`: تعيين عميل
- `lead_transferred`: نقل عميل
- `lead_updated`: تحديث عميل
- `lead_reminder`: تذكير عميل

### إشعارات واتساب
- `whatsapp_message_received`: رسالة واردة
- `whatsapp_template_sent`: إرسال قالب
- `whatsapp_send_failed`: فشل الإرسال
- `whatsapp_waiting_response`: بانتظار الرد

### إشعارات الحملات
- `campaign_performance`: أداء الحملة
- `campaign_low_performance`: انخفاض الأداء
- `campaign_stopped`: إيقاف حملة
- `campaign_budget_alert`: تنبيه الميزانية

### إشعارات المهام
- `task_created`: مهمة جديدة
- `task_reminder`: تذكير مهمة
- `task_completed`: مهمة مكتملة

### إشعارات الصفقات
- `deal_created`: صفقة جديدة
- `deal_updated`: تحديث صفقة
- `deal_closed`: إغلاق صفقة
- `deal_reminder`: تذكير صفقة

### إشعارات التقارير
- `daily_report`: تقرير يومي
- `weekly_report`: تقرير أسبوعي
- `top_employee`: أفضل موظف

### إشعارات النظام
- `login_from_new_device`: تسجيل دخول جديد
- `system_update`: تحديث النظام
- `subscription_expiring`: تنبيه الاشتراك
- `payment_failed`: فشل الدفع
- `subscription_expired`: انتهاء الاشتراك

## ملاحظات

1. **الأمان**: Firebase Admin SDK يتطلب credentials آمنة. لا ترفع ملف credentials إلى Git!
2. **التخزين**: جميع الإشعارات المرسلة تُحفظ في قاعدة البيانات
3. **التكامل التلقائي**: الإشعارات تُرسل تلقائياً عند الأحداث عبر Signals
4. **FCM Token**: يتم تحديث FCM token تلقائياً عند تسجيل الدخول من التطبيق

## الخطوات التالية

1. إعداد Firebase credentials
2. تشغيل migrations
3. اختبار إرسال إشعار تجريبي
4. ربط الإشعارات مع باقي الأحداث (WhatsApp, Campaigns, إلخ)
