# دليل إعداد نظام الإشعارات - Notifications Setup Guide

## 📋 الخطوات المطلوبة

### 1. تثبيت Firebase Admin SDK

```bash
cd CRM-api-1
pip install firebase-admin
```

أو أضف إلى `requirements.txt` (تم إضافته بالفعل):
```
firebase-admin
```

### 2. الحصول على Firebase Service Account Key

1. اذهب إلى [Firebase Console](https://console.firebase.google.com/)
2. اختر مشروعك
3. اذهب إلى **Project Settings** → **Service Accounts**
4. انقر على **Generate New Private Key**
5. احفظ الملف (مثلاً: `firebase-credentials.json`)

### 3. إعداد Credentials

#### للتطوير (Development):
أضف إلى ملف `.env`:
```env
FIREBASE_CREDENTIALS_PATH=/absolute/path/to/firebase-credentials.json
```

#### للإنتاج (Production):
استخدم Environment Variable:
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-credentials.json
```

**⚠️ مهم جداً**: لا ترفع ملف `firebase-credentials.json` إلى Git! أضفه إلى `.gitignore`.

### 4. إنشاء Migrations

```bash
python manage.py makemigrations accounts notifications
python manage.py migrate
```

### 5. اختبار النظام

#### اختبار إرسال إشعار:
```python
python manage.py shell

from accounts.models import User
from notifications.services import NotificationService
from notifications.models import NotificationType

user = User.objects.first()
NotificationService.send_notification(
    user=user,
    notification_type=NotificationType.GENERAL,
    title='اختبار',
    body='هذا إشعار تجريبي',
)
```

## 🔧 API Endpoints

### تحديث FCM Token
```
POST /api/users/update-fcm-token/
Authorization: Bearer <token>
Content-Type: application/json

{
  "fcm_token": "your-fcm-token-here"
}
```

### جلب الإشعارات
```
GET /api/notifications/
GET /api/notifications/?read=false  # غير مقروءة فقط
GET /api/notifications/?type=new_lead  # حسب النوع
```

### تحديد كمقروء
```
POST /api/notifications/{id}/mark_read/
POST /api/notifications/mark_all_read/
```

### عدد غير المقروءة
```
GET /api/notifications/unread_count/
```

## 📱 التكامل مع Flutter

التطبيق يرسل FCM token تلقائياً عند:
- تسجيل الدخول
- تحديث FCM token

جميع API methods جاهزة في `ApiService`:
- `getNotifications()`
- `markNotificationAsRead()`
- `getUnreadNotificationsCount()`
- إلخ

## 🎯 الإشعارات التلقائية

الإشعارات تُرسل تلقائياً عند:
- ✅ إنشاء عميل جديد
- ✅ تغيير حالة العميل
- ✅ تعيين عميل لموظف
- ✅ نقل عميل بين موظفين
- ✅ إنشاء صفقة
- ✅ إغلاق صفقة

## 📝 ملاحظات

1. **Firebase Admin SDK** يجب أن يكون مُثبتاً
2. **Credentials** يجب أن تكون صحيحة
3. **FCM Token** يجب أن يكون محدثاً في قاعدة البيانات
4. جميع الإشعارات تُحفظ في قاعدة البيانات حتى لو فشل الإرسال

## 🔗 روابط مفيدة

- [Firebase Admin SDK Documentation](https://firebase.google.com/docs/admin/setup)
- [FCM Documentation](https://firebase.google.com/docs/cloud-messaging)
