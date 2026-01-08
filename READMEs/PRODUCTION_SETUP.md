# Production Setup Guide - Meta Integration

## 📋 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Generate Encryption Key
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
انسخ النتيجة وأضفها في `.env` كـ `INTEGRATION_ENCRYPTION_KEY`

### 3. Environment Variables
أضف في `.env`:
```env
# Meta Integration
META_CLIENT_ID=your_app_id
META_CLIENT_SECRET=your_app_secret
META_WEBHOOK_VERIFY_TOKEN=your_verify_token
INTEGRATION_ENCRYPTION_KEY=your_encryption_key

# API
API_BASE_URL=https://api.yourdomain.com
```

### 4. Run Migrations
```bash
python manage.py migrate
```

### 5. Setup Background Tasks (Django Q2)
في `settings.py`، تأكد من:
```python
Q_CLUSTER = {
    'name': 'CRM_Queue',
    'workers': 4,
    'recycle': 500,
    'timeout': 60,
    'retry': 120,
    'queue_limit': 50,
    'bulk': 10,
    'orm': 'default',
}
```

إضافة Task لتجديد Tokens:
```python
# في management command أو Django Q2 schedule
from integrations.tasks import refresh_expired_tokens
# جدولة كل ساعة
```

---

## 🔐 Security Checklist

- ✅ Access Tokens مشفرة
- ✅ Webhook signature verification
- ✅ Rate limiting (100 req/min)
- ✅ HTTPS required
- ✅ Environment variables محمية

---

## 📊 Monitoring

### Check Integration Logs
```bash
# في Django shell
from integrations.models import IntegrationLog
IntegrationLog.objects.filter(status='error').order_by('-created_at')[:10]
```

### Check Token Status
```bash
from integrations.models import IntegrationAccount
accounts = IntegrationAccount.objects.filter(status='expired')
```

---

## 🚀 Deployment Steps

1. ✅ Install dependencies
2. ✅ Set environment variables
3. ✅ Run migrations
4. ✅ Setup background tasks
5. ✅ Configure Meta App
6. ✅ Test webhook
7. ✅ Monitor logs

---

## 📝 Next Steps

راجع `PRODUCTION_CHECKLIST.md` للقائمة الكاملة.



