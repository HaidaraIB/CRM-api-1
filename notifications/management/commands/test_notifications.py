"""
Management command to test all notification types
Usage:
    python manage.py test_notifications
    python manage.py test_notifications --user-id 1
    python manage.py test_notifications --type new_lead
    python manage.py test_notifications --all
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from notifications.services import NotificationService
from notifications.models import NotificationType

User = get_user_model()


class Command(BaseCommand):
    help = 'Test notification types by sending test notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='User ID to send notifications to (default: first user)',
        )
        parser.add_argument(
            '--type',
            type=str,
            help='Test specific notification type (e.g., new_lead)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Test all notification types',
        )

    def handle(self, *args, **options):
        # Get user
        user_id = options.get('user_id')
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'User with ID {user_id} not found')
                )
                return
        else:
            user = User.objects.first()
            if not user:
                self.stdout.write(
                    self.style.ERROR('No users found in database')
                )
                return

        self.stdout.write(
            self.style.SUCCESS(f'Testing notifications for user: {user.username} (ID: {user.id})')
        )
        
        # Show user language
        user_language = getattr(user, 'language', 'ar') or 'ar'
        self.stdout.write(
            self.style.SUCCESS(f'User language: {user_language}')
        )

        if not user.fcm_token:
            self.stdout.write(
                self.style.WARNING(
                    f'User {user.username} has no FCM token. '
                    'Notifications will be saved to database but not sent via FCM.'
                )
            )

        # Test specific type
        if options.get('type'):
            notification_type = options.get('type')
            self._test_notification(user, notification_type)
            return

        # Test all types
        if options.get('all'):
            self._test_all_notifications(user)
            return

        # Default: show menu
        self._show_menu(user)

    def _show_menu(self, user):
        """Show interactive menu"""
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write('Notification Types Test Menu')
        self.stdout.write('=' * 60 + '\n')

        categories = {
            'Core Notifications': [
                ('new_lead', 'New Lead'),
                ('lead_no_follow_up', 'Lead No Follow Up'),
                ('lead_reengaged', 'Lead Reengaged'),
                ('lead_contact_failed', 'Lead Contact Failed'),
                ('lead_status_changed', 'Lead Status Changed'),
                ('lead_assigned', 'Lead Assigned'),
                ('lead_transferred', 'Lead Transferred'),
                ('lead_updated', 'Lead Updated'),
                ('lead_reminder', 'Lead Reminder'),
            ],
            'WhatsApp Notifications': [
                ('whatsapp_message_received', 'WhatsApp Message Received'),
                ('whatsapp_template_sent', 'WhatsApp Template Sent'),
                ('whatsapp_send_failed', 'WhatsApp Send Failed'),
                ('whatsapp_waiting_response', 'WhatsApp Waiting Response'),
            ],
            'Campaign Notifications': [
                ('campaign_performance', 'Campaign Performance'),
                ('campaign_low_performance', 'Campaign Low Performance'),
                ('campaign_stopped', 'Campaign Stopped'),
                ('campaign_budget_alert', 'Campaign Budget Alert'),
            ],
            'Team & Tasks': [
                ('task_created', 'Task Created'),
                ('task_reminder', 'Task Reminder'),
                ('task_completed', 'Task Completed'),
            ],
            'Deals': [
                ('deal_created', 'Deal Created'),
                ('deal_updated', 'Deal Updated'),
                ('deal_closed', 'Deal Closed'),
                ('deal_reminder', 'Deal Reminder'),
            ],
            'Reports': [
                ('daily_report', 'Daily Report'),
                ('weekly_report', 'Weekly Report'),
                ('top_employee', 'Top Employee'),
            ],
            'System & Subscription': [
                ('login_from_new_device', 'Login from New Device'),
                ('system_update', 'System Update'),
                ('subscription_expiring', 'Subscription Expiring'),
                ('payment_failed', 'Payment Failed'),
                ('subscription_expired', 'Subscription Expired'),
            ],
            'General': [
                ('general', 'General'),
            ],
        }

        index = 1
        type_map = {}

        for category, types in categories.items():
            self.stdout.write(f'\n{category}:')
            for type_key, type_name in types:
                type_map[index] = type_key
                self.stdout.write(f'  {index}. {type_name} ({type_key})')
                index += 1

        self.stdout.write(f'\n{index}. Test All Types')
        self.stdout.write('0. Exit\n')

        self.stdout.write(
            f'\nTo test a specific type, run:\n'
            f'  python manage.py test_notifications --type <type_key>\n'
            f'  python manage.py test_notifications --all\n'
        )

    def _test_notification(self, user, notification_type):
        """Test a specific notification type"""
        # Validate type
        valid_types = [choice[0] for choice in NotificationType.choices]
        if notification_type not in valid_types:
            self.stdout.write(
                self.style.ERROR(
                    f'Invalid notification type: {notification_type}\n'
                    f'Valid types: {", ".join(valid_types)}'
                )
            )
            return

        # Get type display name
        type_display = dict(NotificationType.choices).get(notification_type, notification_type)

        self.stdout.write(f'\nTesting: {type_display} ({notification_type})')
        self.stdout.write('-' * 60)
        self.stdout.write(f'User language: {getattr(user, "language", "ar")}')

        # Prepare test data based on type
        # Note: We don't pass title/body - translations will be used automatically based on user.language
        _, _, data = self._get_test_data(notification_type)

        try:
            # لا نرسل title و body - سيتم استخدام الترجمات تلقائياً بناءً على user.language
            result = NotificationService.send_notification(
                user=user,
                notification_type=notification_type,
                data=data,
            )

            if result:
                self.stdout.write(
                    self.style.SUCCESS('Notification sent successfully!')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        'Notification saved to database but may not have been sent via FCM '
                        '(check FCM token and Firebase credentials)'
                    )
                )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error sending notification: {e}')
            )

    def _test_all_notifications(self, user):
        """Test all notification types"""
        self.stdout.write('\nTesting all notification types...\n')
        self.stdout.write('=' * 60)

        all_types = [choice[0] for choice in NotificationType.choices]
        success_count = 0
        failed_count = 0

        for notification_type in all_types:
            type_display = dict(NotificationType.choices).get(notification_type, notification_type)
            self.stdout.write(f'\n[{success_count + failed_count + 1}/{len(all_types)}] Testing: {type_display}')

            _, _, data = self._get_test_data(notification_type)

            try:
                # لا نرسل title و body - سيتم استخدام الترجمات تلقائياً
                result = NotificationService.send_notification(
                    user=user,
                    notification_type=notification_type,
                    data=data,
                )

                if result:
                    self.stdout.write(self.style.SUCCESS('  OK'))
                    success_count += 1
                else:
                    self.stdout.write(self.style.WARNING('  Saved (FCM may have failed)'))
                    success_count += 1  # Still counts as success (saved to DB)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  FAILED: {e}'))
                failed_count += 1

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f'\nTest completed: {success_count} succeeded, {failed_count} failed'
            )
        )

    def _get_test_data(self, notification_type):
        """Get test data for a notification type (translations will be used automatically)"""
        test_data = {
            'new_lead': (
                'عميل محتمل جديد',
                'تم إضافة عميل محتمل جديد من حملة فيسبوك',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'campaign_name': 'حملة فيسبوك'}
            ),
            'lead_no_follow_up': (
                'بدون متابعة',
                'عميل محتمل لم يتم التواصل معه منذ 30 دقيقة',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'minutes': 30}
            ),
            'lead_reengaged': (
                'إعادة تفاعل',
                'عميل محتمل سابق عاد وتفاعل مرة أخرى',
                {'lead_id': 123, 'lead_name': 'أحمد محمد'}
            ),
            'lead_contact_failed': (
                'فشل التواصل',
                'لم يتم الرد بعد 3 محاولات اتصال',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'attempts': 3}
            ),
            'lead_status_changed': (
                'تغيير الحالة',
                'تم تغيير حالة العميل المحتمل إلى "قيد المتابعة"',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'new_status': 'قيد المتابعة'}
            ),
            'lead_assigned': (
                'تعيين عميل محتمل',
                'تم تعيين عميل محتمل جديد لك',
                {'lead_id': 123, 'lead_name': 'أحمد محمد'}
            ),
            'lead_transferred': (
                'نقل عميل محتمل',
                'تم نقل العميل المحتمل إلى موظف آخر',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'from_employee': 'موظف 1', 'to_employee': 'موظف 2'}
            ),
            'lead_updated': (
                'تحديث عميل',
                'تم تحديث معلومات العميل المحتمل',
                {'lead_id': 123, 'lead_name': 'أحمد محمد'}
            ),
            'lead_reminder': (
                'تذكير عميل',
                'تذكير بموعد متابعة العميل المحتمل',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'reminder_time': '2024-01-01 10:00'}
            ),
            'whatsapp_message_received': (
                'رسالة واتساب واردة',
                'رسالة جديدة من عميل محتمل عبر واتساب',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'message': 'مرحبا'}
            ),
            'whatsapp_template_sent': (
                'إرسال قالب واتساب',
                'تم إرسال رسالة الترحيب بنجاح',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'template_name': 'ترحيب'}
            ),
            'whatsapp_send_failed': (
                'فشل إرسال واتساب',
                'فشل إرسال قالب واتساب',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'error': 'Connection timeout'}
            ),
            'whatsapp_waiting_response': (
                'بانتظار الرد',
                'لا يوجد رد من العميل المحتمل منذ 24 ساعة',
                {'lead_id': 123, 'lead_name': 'أحمد محمد', 'hours': 24}
            ),
            'campaign_performance': (
                'أداء الحملة',
                'الحملة حققت 100 عميل محتمل',
                {'campaign_id': 456, 'campaign_name': 'حملة فيسبوك', 'leads_count': 100}
            ),
            'campaign_low_performance': (
                'انخفاض الأداء',
                'انخفاض عدد العملاء المحتملين اليوم',
                {'campaign_id': 456, 'campaign_name': 'حملة فيسبوك', 'today_leads': 5}
            ),
            'campaign_stopped': (
                'إيقاف حملة',
                'تم إيقاف الحملة بسبب نفاد الميزانية',
                {'campaign_id': 456, 'campaign_name': 'حملة فيسبوك', 'reason': 'نفاد الميزانية'}
            ),
            'campaign_budget_alert': (
                'تنبيه الميزانية',
                'الميزانية المتبقية أقل من 20%',
                {'campaign_id': 456, 'campaign_name': 'حملة فيسبوك', 'remaining_percent': 15}
            ),
            'task_created': (
                'مهمة جديدة',
                'لديك مهمة متابعة اليوم',
                {'task_id': 789, 'task_title': 'متابعة عميل', 'due_date': '2024-01-01'}
            ),
            'task_reminder': (
                'تذكير مهمة',
                'تبقى 30 دقيقة على موعد المتابعة',
                {'task_id': 789, 'task_title': 'متابعة عميل', 'minutes_remaining': 30}
            ),
            'task_completed': (
                'مهمة مكتملة',
                'تم إكمال المهمة بنجاح',
                {'task_id': 789, 'task_title': 'متابعة عميل'}
            ),
            'deal_created': (
                'صفقة جديدة',
                'تم إنشاء صفقة جديدة',
                {'deal_id': 101, 'deal_title': 'صفقة أحمد محمد', 'value': 50000}
            ),
            'deal_updated': (
                'تحديث صفقة',
                'تم تحديث معلومات الصفقة',
                {'deal_id': 101, 'deal_title': 'صفقة أحمد محمد'}
            ),
            'deal_closed': (
                'إغلاق صفقة',
                'تم إغلاق الصفقة بنجاح',
                {'deal_id': 101, 'deal_title': 'صفقة أحمد محمد', 'value': 50000}
            ),
            'deal_reminder': (
                'تذكير صفقة',
                'تذكير بموعد متابعة الصفقة',
                {'deal_id': 101, 'deal_title': 'صفقة أحمد محمد'}
            ),
            'daily_report': (
                'تقرير يومي',
                'اليوم: 32 عميل محتمل – 5 مبيعات',
                {'date': '2024-01-01', 'leads_count': 32, 'deals_count': 5}
            ),
            'weekly_report': (
                'تقرير أسبوعي',
                'تقرير الأداء الأسبوعي جاهز',
                {'week': '2024-W01', 'leads_count': 200, 'deals_count': 30}
            ),
            'top_employee': (
                'أفضل موظف',
                'أفضل موظف مبيعات لهذا الأسبوع',
                {'employee_id': 1, 'employee_name': 'أحمد', 'deals_count': 15}
            ),
            'login_from_new_device': (
                'تسجيل دخول جديد',
                'تم تسجيل دخول من جهاز جديد',
                {'device': 'iPhone 14', 'location': 'Baghdad', 'ip': '192.168.1.1'}
            ),
            'system_update': (
                'تحديث النظام',
                'تم إضافة ميزة جديدة إلى Loop CRM',
                {'version': '2.0.0', 'feature': 'نظام الإشعارات'}
            ),
            'subscription_expiring': (
                'تنبيه الاشتراك',
                'اشتراكك ينتهي خلال 3 أيام',
                {'days_remaining': 3, 'expiry_date': '2024-01-04'}
            ),
            'payment_failed': (
                'فشل الدفع',
                'فشل عملية الدفع، يرجى التحقق',
                {'payment_id': 999, 'amount': 100, 'reason': 'Insufficient funds'}
            ),
            'subscription_expired': (
                'انتهاء الاشتراك',
                'انتهى الاشتراك، يرجى التجديد',
                {'expiry_date': '2024-01-01'}
            ),
            'general': (
                'إشعار عام',
                'هذا إشعار تجريبي',
                {'test': True}
            ),
        }

        return test_data.get(notification_type, (
            f'Test Notification: {notification_type}',
            'This is a test notification',
            {'test': True, 'type': notification_type}
        ))
