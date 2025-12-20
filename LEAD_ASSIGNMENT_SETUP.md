# إعداد نظام التوزيع التلقائي وإعادة التعيين

## الخطوات المطلوبة بعد التحديثات

### 1. تثبيت المكتبات الجديدة
```bash
pip install -r requirements.txt
```

### 2. إنشاء Migrations
```bash
python manage.py makemigrations companies
python manage.py makemigrations crm
```

### 3. تطبيق Migrations
```bash
python manage.py migrate
```

### 4. جدولة مهمة إعادة التعيين
بعد تطبيق migrations، قم بتشغيل الأمر التالي **مرة واحدة فقط** لجدولة مهمة إعادة التعيين:
```bash
python manage.py setup_reassign_schedule
```

### 5. تشغيل Django Q Worker
يجب تشغيل Django Q Worker في عملية منفصلة لتنفيذ المهام المجدولة:

**في بيئة التطوير (Terminal منفصل):**
```bash
python manage.py qcluster
```

**في بيئة الإنتاج:**
يجب إضافة Django Q Worker كخدمة (service) أو process manager (مثل supervisor أو systemd).

## كيفية عمل النظام

### Auto Assign (التوزيع التلقائي)
- **متى يعمل**: عند إنشاء عميل جديد (Client) عبر API
- **كيف يعمل**: 
  - يتحقق من إعداد `auto_assign_enabled` في الشركة
  - يبحث عن الموظف الذي لديه أقل عدد من العملاء المعينين
  - يعين العميل الجديد لهذا الموظف تلقائياً
  - يتم تحديث `assigned_at` تلقائياً

### Re-assign (إعادة التعيين)
- **متى يعمل**: كل ساعة (مجدولة عبر django-q2)
- **كيف يعمل**:
  - يبحث عن جميع الشركات التي لديها `re_assign_enabled = True`
  - لكل شركة، يبحث عن العملاء الذين:
    - تم تعيينهم منذ أكثر من `re_assign_hours` ساعة
    - لم يتم التواصل معهم منذ `re_assign_hours` ساعة (أو لم يتم التواصل معهم أبداً)
  - يعيد تعيينهم للموظف الأقل انشغالاً
  - يسجل الحدث في `ClientEvent`

### تتبع آخر تواصل
- يتم تحديث `last_contacted_at` تلقائياً عند:
  - إنشاء `ClientTask` جديد (Activity/Action)
  - يتم التحديث عبر Django Signal

## API Endpoints

### تحديث إعدادات التوزيع
```
PATCH /api/companies/{id}/update_assignment_settings/
Body: {
    "auto_assign_enabled": true/false,
    "re_assign_enabled": true/false,
    "re_assign_hours": 24
}
```

## ملاحظات مهمة

1. **Django Q Worker**: يجب أن يعمل دائماً في الخلفية. بدون تشغيله، لن تعمل المهام المجدولة (Re-assign).

2. **الأداء**: المهمة المجدولة تعمل كل ساعة. إذا كان لديك عدد كبير جداً من الشركات والعملاء، قد تحتاج لتعديل التكرار.

3. **الاختبار**: يمكنك اختبار Re-assign يدوياً عبر:
   ```python
   from crm.tasks import re_assign_inactive_clients
   re_assign_inactive_clients()
   ```

