# Production Checklist - Push Notifications System

## ✅ Database Migrations

### 1. User Model Fields
- [x] Migration `0012_user_fcm_token.py` - FCM token field
- [x] Migration `0013_user_language.py` - Language preference field
- [ ] Run migrations in production:
  ```bash
  python manage.py migrate accounts
  python manage.py migrate notifications
  ```

### 2. Notification Model
- [x] Migration `0001_initial.py` - Notification model
- [ ] Verify indexes are created:
  - `user + created_at`
  - `user + read`
  - `type`

---

## ✅ Firebase Configuration

### 1. Firebase Admin SDK Credentials
- [ ] Create Firebase project in Firebase Console
- [ ] Generate service account key (JSON file)
- [ ] Set environment variable in production:
  ```bash
  export FIREBASE_CREDENTIALS_PATH=/path/to/firebase-service-account.json
  # OR
  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json
  ```
- [ ] Add to `.env` file (if using):
  ```env
  FIREBASE_CREDENTIALS_PATH=/path/to/firebase-service-account.json
  ```

### 2. Firebase Project Settings
- [ ] Enable Cloud Messaging API in Firebase Console
- [ ] Add server key to Django settings (if needed)
- [ ] Configure FCM sender ID

### 3. Flutter App Configuration
- [ ] `google-services.json` added to `android/app/`
- [ ] `GoogleService-Info.plist` added to `ios/Runner/`
- [ ] Firebase project linked to Flutter app
- [ ] APNs certificates configured (for iOS)

---

## ✅ Environment Variables

### Django Backend (.env)
```env
# Firebase
FIREBASE_CREDENTIALS_PATH=/path/to/firebase-service-account.json
# OR
GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json

# Django Settings
DEBUG=False
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=api.yourdomain.com,www.api.yourdomain.com
```

### Flutter App (.env)
```env
BASE_URL=https://api.yourdomain.com/api
API_KEY=your-api-key
```

---

## ✅ Security

### 1. API Endpoints
- [x] All notification endpoints require authentication (`IsAuthenticated`)
- [x] `send_notification` endpoint restricted to admins only
- [x] User can only access their own notifications

### 2. Firebase Credentials
- [ ] Service account JSON file secured (not in git)
- [ ] File permissions: `chmod 600 firebase-service-account.json`
- [ ] Credentials stored securely (environment variables or secret manager)

### 3. FCM Tokens
- [x] Tokens stored securely in database
- [x] Tokens updated only by authenticated users
- [ ] Consider token encryption for sensitive data

---

## ✅ Error Handling & Logging

### 1. Logging
- [x] All notification operations logged
- [x] Firebase initialization errors logged
- [x] FCM send failures logged
- [ ] Set up log aggregation (e.g., Sentry, Loggly)

### 2. Error Handling
- [x] Graceful fallback if Firebase not initialized
- [x] Notifications saved to DB even if FCM fails
- [x] User-friendly error messages
- [ ] Retry mechanism for failed FCM sends (optional)

### 3. Monitoring
- [ ] Monitor notification success rate
- [ ] Alert on high failure rates
- [ ] Track FCM token validity
- [ ] Monitor Firebase quota usage

---

## ✅ Performance

### 1. Database
- [x] Indexes on notification queries
- [ ] Consider pagination for notification lists
- [ ] Cleanup old notifications (optional)

### 2. FCM Sending
- [x] Batch sending for multiple users
- [ ] Rate limiting for FCM API calls
- [ ] Async task queue for bulk notifications (optional)

### 3. Caching
- [ ] Cache user language preferences
- [ ] Cache notification settings (optional)

---

## ✅ Localization

### 1. Translations
- [x] All notification types have Arabic and English translations
- [x] User language preference stored in database
- [x] Language updated when user changes it in app
- [ ] Test all notification types in both languages

### 2. Translation Files
- [x] `notifications/translations.py` contains all translations
- [ ] Verify all notification types are translated
- [ ] Test with real data to ensure formatting works

---

## ✅ Testing

### 1. Unit Tests
- [ ] Test notification sending
- [ ] Test translation system
- [ ] Test language updates
- [ ] Test FCM token updates

### 2. Integration Tests
- [ ] Test end-to-end notification flow
- [ ] Test notification delivery to Flutter app
- [ ] Test notification routing in app
- [ ] Test notification settings

### 3. Manual Testing
- [ ] Test all notification types
- [ ] Test in both languages (ar/en)
- [ ] Test on Android and iOS
- [ ] Test background notifications
- [ ] Test foreground notifications
- [ ] Test notification actions (tap, dismiss)

---

## ✅ Flutter App Configuration

### 1. Dependencies
- [x] `firebase_core: ^3.6.0`
- [x] `firebase_messaging: ^15.1.3`
- [x] `flutter_local_notifications`
- [x] `timezone`
- [x] `shared_preferences`
- [ ] Verify all dependencies are up to date

### 2. Android Configuration
- [x] `google-services.json` in `android/app/`
- [x] Google Services plugin in `build.gradle.kts`
- [x] Notification channels configured
- [ ] Test on Android devices

### 3. iOS Configuration
- [x] `GoogleService-Info.plist` in `ios/Runner/`
- [x] APNs certificates configured
- [x] Push notification capabilities enabled
- [ ] Test on iOS devices

### 4. Permissions
- [x] Notification permissions requested
- [x] Background message handler configured
- [x] Foreground message handler configured

---

## ✅ Notification Settings

### 1. User Preferences
- [x] Global enable/disable notifications
- [x] Per-type notification toggles
- [x] Time-based restrictions
- [x] Settings persisted locally and synced

### 2. Settings UI
- [x] Notification settings screen
- [x] Test notification feature
- [x] Localized UI
- [ ] User documentation for settings

---

## ✅ API Endpoints

### 1. Notification Management
- [x] `GET /api/notifications/` - List notifications
- [x] `GET /api/notifications/{id}/` - Get notification
- [x] `POST /api/notifications/{id}/mark_read/` - Mark as read
- [x] `POST /api/notifications/mark_all_read/` - Mark all as read
- [x] `GET /api/notifications/unread_count/` - Get unread count
- [x] `DELETE /api/notifications/delete_all_read/` - Delete read notifications

### 2. User Preferences
- [x] `POST /api/users/update-fcm-token/` - Update FCM token
- [x] `POST /api/users/update-language/` - Update language

### 3. Admin
- [x] `POST /api/notifications/send/` - Send notification (admin only)

---

## ✅ Automatic Notifications (Signals)

### 1. CRM Events
- [x] New lead created
- [x] Lead status changed
- [x] Lead assigned
- [x] Lead transferred
- [x] Lead updated
- [ ] Test all signal triggers

### 2. Other Events
- [ ] Deal created/updated/closed
- [ ] Task created/completed
- [ ] Campaign events
- [ ] WhatsApp events
- [ ] System events

---

## ✅ Documentation

### 1. Setup Guides
- [x] `FIREBASE_SETUP.md` - Firebase setup guide
- [x] `NOTIFICATIONS_GUIDE.md` - Notification system guide
- [ ] API documentation updated

### 2. User Documentation
- [ ] How to enable/disable notifications
- [ ] How to customize notification settings
- [ ] Troubleshooting guide

---

## ✅ Deployment Steps

### 1. Pre-Deployment
```bash
# 1. Run migrations
python manage.py migrate

# 2. Collect static files
python manage.py collectstatic --noinput

# 3. Verify Firebase credentials
python manage.py shell
>>> from notifications.services import NotificationService
>>> NotificationService.initialize()
True
```

### 2. Environment Setup
- [ ] Set `DEBUG=False` in production
- [ ] Set `ALLOWED_HOSTS` correctly
- [ ] Set Firebase credentials path
- [ ] Verify environment variables

### 3. Post-Deployment
- [ ] Test notification sending
- [ ] Monitor logs for errors
- [ ] Verify FCM tokens are being saved
- [ ] Test language updates
- [ ] Monitor Firebase quota

---

## ✅ Monitoring & Alerts

### 1. Key Metrics
- [ ] Notification success rate
- [ ] FCM token registration rate
- [ ] Language distribution
- [ ] Notification read rate

### 2. Alerts
- [ ] Alert on Firebase initialization failure
- [ ] Alert on high FCM failure rate
- [ ] Alert on missing FCM tokens
- [ ] Alert on translation errors

---

## ✅ Backup & Recovery

### 1. Database
- [ ] Regular backups of notification data
- [ ] Backup user preferences (language, FCM tokens)

### 2. Firebase
- [ ] Backup service account credentials
- [ ] Document Firebase project settings

---

## 🔧 Troubleshooting

### Notifications Not Sending
1. Check Firebase initialization logs
2. Verify FCM tokens in database
3. Check Firebase credentials path
4. Verify Firebase project settings
5. Check FCM API quota

### Wrong Language
1. Verify `user.language` field in database
2. Check translation files
3. Verify `get_notification_text` function
4. Test with different users

### FCM Tokens Null
1. Check Flutter app logs
2. Verify Firebase configuration in app
3. Check API endpoint `/api/users/update-fcm-token/`
4. Verify user is logged in

### Notifications Not Appearing
1. Check notification permissions
2. Verify notification channels (Android)
3. Check background message handler
4. Verify notification routing

---

## 📞 Support

For issues:
1. Check Django logs
2. Check Flutter app logs
3. Check Firebase Console
4. Review this checklist
5. Check notification database records

---

## ✅ Final Checklist

Before going to production:
- [ ] All migrations applied
- [ ] Firebase credentials configured
- [ ] Environment variables set
- [ ] All tests passing
- [ ] Manual testing completed
- [ ] Documentation reviewed
- [ ] Monitoring set up
- [ ] Backup strategy in place
- [ ] Security review completed
- [ ] Performance tested

---

**Last Updated:** $(date)
**Version:** 1.0.0
