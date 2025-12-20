# كيفية إيقاف Django Q Worker بشكل كامل

## المشكلة
Django Q Worker يعيد تشغيل نفسه تلقائياً حتى بعد الضغط على Ctrl+C.

## الحلول

### الحل 1: استخدام Task Manager
1. افتح Task Manager (Ctrl+Shift+Esc)
2. ابحث عن عمليات Python التي تعمل
3. أوقف جميع عمليات Python التي تحتوي على "qcluster" في Command Line

### الحل 2: استخدام PowerShell
```powershell
Get-Process python | Where-Object {$_.Path -like "*CRM-api-1*"} | Stop-Process -Force
```

### الحل 3: تنظيف قاعدة البيانات أولاً
```bash
python manage.py cleanup_all_tasks --force
python manage.py setup_reassign_schedule
```

ثم أعد تشغيل Worker:
```bash
python manage.py qcluster
```

### الحل 4: إيقاف Worker من Django Admin
1. افتح Django Admin
2. اذهب إلى Django Q > Schedules
3. احذف أي جدولة تحتوي على `check_reassignments`
4. اذهب إلى Django Q > Tasks
5. احذف أي مهمة تحتوي على `check_reassignments`

## بعد الإيقاف
بعد إيقاف Worker، قم بتنظيف قاعدة البيانات:
```bash
python manage.py cleanup_all_tasks --force
python manage.py setup_reassign_schedule
```

ثم أعد تشغيل Worker:
```bash
python manage.py qcluster
```

