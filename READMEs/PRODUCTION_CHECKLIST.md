# Production Checklist - Meta Integration

## ✅ Security

### 1. Encryption Keys
- [ ] إضافة `INTEGRATION_ENCRYPTION_KEY` في `.env`
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- [ ] التأكد من أن `.env` في `.gitignore`
- [ ] نسخ احتياطي آمن لـ Encryption Key

### 2. Webhook Security
- [ ] Webhook URL يستخدم HTTPS فقط
- [ ] التحقق من التوقيع (X-Hub-Signature-256) مفعّل ✅
- [ ] Rate Limiting مفعّل ✅ (100 requests/minute)

### 3. Access Tokens
- [ ] Tokens مشفرة في قاعدة البيانات ✅
- [ ] لا يتم عرض Tokens في API responses ✅

---

## ✅ Error Handling & Monitoring

### 1. Logging
- [ ] جميع العمليات تُسجّل في `IntegrationLog` ✅
- [ ] Django logs مفعّل
- [ ] Error tracking (Sentry أو similar) - اختياري

### 2. Retry Mechanism
- [ ] Background task لتجديد Tokens ✅
- [ ] Retry logic للـ API calls (اختياري)

### 3. Alerts
- [ ] إشعارات عند فشل Webhook
- [ ] إشعارات عند انتهاء Tokens
- [ ] Monitoring للـ IntegrationLog errors

---

## ✅ Background Tasks

### 1. Token Refresh
- [ ] إعداد Django Q2 أو Celery
- [ ] جدولة `refresh_expired_tokens()` كل ساعة
  ```python
  # في Django Q2
  from integrations.tasks import refresh_expired_tokens
  schedule('refresh_tokens', func=refresh_expired_tokens, schedule_type=Schedule.HOURLY)
  ```

### 2. Monitoring Tasks
- [ ] فحص الحسابات المنقطعة
- [ ] تنظيف Logs القديمة (اختياري)

---

## ✅ Meta App Configuration

### 1. App Settings
- [ ] App في Production Mode (ليس Development)
- [ ] App Review مكتمل للصلاحيات المطلوبة
- [ ] Valid OAuth Redirect URIs مضبوط
- [ ] Webhook URL مضبوط وصحيح

### 2. Permissions
- [ ] `leads_retrieval` ✅
- [ ] `pages_show_list` ✅
- [ ] `pages_read_engagement` ✅
- [ ] `business_management` ✅

### 3. Webhook Subscriptions
- [ ] `leadgen` subscription مفعّل ✅

---

## ✅ Database

### 1. Migrations
- [ ] جميع Migrations مطبقة ✅
- [ ] Backup strategy للبيانات

### 2. Indexes
- [ ] فحص Indexes على:
  - `IntegrationAccount(company, platform)`
  - `IntegrationAccount(status)`
  - `Client(integration_account)`

---

## ✅ Testing

### 1. Unit Tests
- [ ] Tests للـ webhook verification
- [ ] Tests للـ OAuth flow
- [ ] Tests للـ encryption/decryption

### 2. Integration Tests
- [ ] Test استقبال ليد من Meta
- [ ] Test ربط Lead Form بكامبين
- [ ] Test Auto-assignment

### 3. Load Testing
- [ ] Test Webhook تحت حمل عالي
- [ ] Test Rate Limiting

---

## ✅ Documentation

### 1. API Documentation
- [ ] Swagger/OpenAPI docs محدثة
- [ ] Examples للـ endpoints

### 2. User Documentation
- [ ] دليل إعداد Meta App ✅ (`META_INTEGRATION_SETUP.md`)
- [ ] دليل استخدام في Frontend

---

## ✅ Environment Variables

```env
# Meta Integration
META_CLIENT_ID=your_app_id
META_CLIENT_SECRET=your_app_secret
META_WEBHOOK_VERIFY_TOKEN=your_verify_token
INTEGRATION_ENCRYPTION_KEY=your_encryption_key

# API
API_BASE_URL=https://api.yourdomain.com
FRONTEND_URL=https://app.yourdomain.com
```

---

## ✅ Deployment

### 1. Server Configuration
- [ ] HTTPS مفعّل
- [ ] CORS مضبوط بشكل صحيح
- [ ] Allowed Hosts محدثة

### 2. Monitoring
- [ ] Server monitoring (CPU, Memory, Disk)
- [ ] Database monitoring
- [ ] API response times

### 3. Backup
- [ ] Database backups منتظمة
- [ ] Encryption keys backup آمن

---

## ✅ Post-Deployment

### 1. Verification
- [ ] اختبار Webhook verification
- [ ] اختبار OAuth flow
- [ ] اختبار استقبال ليد

### 2. Monitoring
- [ ] مراقبة IntegrationLog للأخطاء
- [ ] مراقبة Token refresh
- [ ] مراقبة Webhook success rate

---

## 🔧 Troubleshooting

### Webhook Not Receiving Leads
1. تحقق من Webhook URL في Meta App
2. تحقق من `leadgen` subscription
3. تحقق من IntegrationLog للأخطاء
4. تحقق من أن الشركة ربطت Lead Form (`select_lead_form`)

### Tokens Expiring
1. تحقق من Background Task (Token Refresh)
2. تحقق من Refresh Token
3. تحقق من IntegrationLog

### Rate Limiting Issues
1. تحقق من Redis/Cache configuration
2. زيادة Rate Limit إذا لزم الأمر

---

## 📞 Support

في حالة المشاكل:
1. تحقق من `IntegrationLog`
2. تحقق من Django logs
3. تحقق من Meta App Dashboard
4. راجع هذا الدليل



