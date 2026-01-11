"""
Notification translations for different languages
"""
from typing import Dict

# ترجمات الإشعارات
NOTIFICATION_TRANSLATIONS: Dict[str, Dict[str, Dict[str, str]]] = {
    'new_lead': {
        'ar': {
            'title': 'عميل محتمل جديد',
            'body': 'تم إضافة عميل محتمل جديد من حملة {campaign_name}'
        },
        'en': {
            'title': 'New Lead',
            'body': 'A new lead has been added from campaign {campaign_name}'
        }
    },
    'lead_no_follow_up': {
        'ar': {
            'title': 'بدون متابعة',
            'body': 'عميل محتمل لم يتم التواصل معه منذ {minutes} دقيقة'
        },
        'en': {
            'title': 'No Follow Up',
            'body': 'A lead has not been contacted for {minutes} minutes'
        }
    },
    'lead_reengaged': {
        'ar': {
            'title': 'إعادة تفاعل',
            'body': 'عميل محتمل سابق عاد وتفاعل مرة أخرى'
        },
        'en': {
            'title': 'Lead Reengaged',
            'body': 'A previous lead has reengaged'
        }
    },
    'lead_contact_failed': {
        'ar': {
            'title': 'فشل التواصل',
            'body': 'لم يتم الرد بعد {attempts} محاولات اتصال'
        },
        'en': {
            'title': 'Contact Failed',
            'body': 'No response after {attempts} contact attempts'
        }
    },
    'lead_status_changed': {
        'ar': {
            'title': 'تغيير الحالة',
            'body': 'تم تغيير حالة العميل المحتمل إلى "{new_status}"'
        },
        'en': {
            'title': 'Status Changed',
            'body': 'Lead status has been changed to "{new_status}"'
        }
    },
    'lead_assigned': {
        'ar': {
            'title': 'تم تعيين عميل محتمل جديد',
            'body': 'تم تعيين العميل {lead_name} لك'
        },
        'en': {
            'title': 'Lead Assigned',
            'body': 'Lead {lead_name} has been assigned to you'
        }
    },
    'lead_transferred': {
        'ar': {
            'title': 'نقل عميل محتمل',
            'body': 'تم نقل العميل {lead_name} منك إلى {to_employee}'
        },
        'en': {
            'title': 'Lead Transferred',
            'body': 'Lead {lead_name} has been transferred from you to {to_employee}'
        }
    },
    'lead_updated': {
        'ar': {
            'title': 'تحديث عميل',
            'body': 'تم تحديث معلومات العميل {lead_name}'
        },
        'en': {
            'title': 'Lead Updated',
            'body': 'Lead {lead_name} has been updated'
        }
    },
    'lead_reminder': {
        'ar': {
            'title': 'تذكير عميل',
            'body': 'تذكير بموعد متابعة العميل {lead_name}'
        },
        'en': {
            'title': 'Lead Reminder',
            'body': 'Reminder to follow up with lead {lead_name}'
        }
    },
    'whatsapp_message_received': {
        'ar': {
            'title': 'رسالة واتساب واردة',
            'body': 'رسالة جديدة من عميل محتمل عبر واتساب'
        },
        'en': {
            'title': 'WhatsApp Message Received',
            'body': 'New message from a lead via WhatsApp'
        }
    },
    'whatsapp_template_sent': {
        'ar': {
            'title': 'إرسال قالب واتساب',
            'body': 'تم إرسال رسالة الترحيب بنجاح'
        },
        'en': {
            'title': 'WhatsApp Template Sent',
            'body': 'Welcome message has been sent successfully'
        }
    },
    'whatsapp_send_failed': {
        'ar': {
            'title': 'فشل إرسال واتساب',
            'body': 'فشل إرسال قالب واتساب'
        },
        'en': {
            'title': 'WhatsApp Send Failed',
            'body': 'Failed to send WhatsApp template'
        }
    },
    'whatsapp_waiting_response': {
        'ar': {
            'title': 'بانتظار الرد',
            'body': 'لا يوجد رد من العميل المحتمل منذ {hours} ساعة'
        },
        'en': {
            'title': 'Waiting for Response',
            'body': 'No response from lead for {hours} hours'
        }
    },
    'campaign_performance': {
        'ar': {
            'title': 'أداء الحملة',
            'body': 'الحملة {campaign_name} حققت {leads_count} عميل محتمل'
        },
        'en': {
            'title': 'Campaign Performance',
            'body': 'Campaign {campaign_name} has achieved {leads_count} leads'
        }
    },
    'campaign_low_performance': {
        'ar': {
            'title': 'انخفاض الأداء',
            'body': 'انخفاض عدد العملاء المحتملين اليوم في حملة {campaign_name}'
        },
        'en': {
            'title': 'Low Performance',
            'body': 'Low number of leads today in campaign {campaign_name}'
        }
    },
    'campaign_stopped': {
        'ar': {
            'title': 'إيقاف حملة',
            'body': 'تم إيقاف الحملة {campaign_name} بسبب {reason}'
        },
        'en': {
            'title': 'Campaign Stopped',
            'body': 'Campaign {campaign_name} has been stopped due to {reason}'
        }
    },
    'campaign_budget_alert': {
        'ar': {
            'title': 'تنبيه الميزانية',
            'body': 'الميزانية المتبقية في حملة {campaign_name} أقل من {remaining_percent}%'
        },
        'en': {
            'title': 'Budget Alert',
            'body': 'Remaining budget in campaign {campaign_name} is less than {remaining_percent}%'
        }
    },
    'task_created': {
        'ar': {
            'title': 'مهمة جديدة',
            'body': 'لديك مهمة متابعة جديدة: {task_title}'
        },
        'en': {
            'title': 'New Task',
            'body': 'You have a new follow-up task: {task_title}'
        }
    },
    'task_reminder': {
        'ar': {
            'title': 'تذكير مهمة',
            'body': 'تبقى {minutes_remaining} دقيقة على موعد المتابعة: {task_title}'
        },
        'en': {
            'title': 'Task Reminder',
            'body': '{minutes_remaining} minutes remaining for follow-up: {task_title}'
        }
    },
    'task_completed': {
        'ar': {
            'title': 'مهمة مكتملة',
            'body': 'تم إكمال المهمة: {task_title}'
        },
        'en': {
            'title': 'Task Completed',
            'body': 'Task completed: {task_title}'
        }
    },
    'deal_created': {
        'ar': {
            'title': 'صفقة جديدة',
            'body': 'تم إنشاء صفقة جديدة: {deal_title}'
        },
        'en': {
            'title': 'New Deal',
            'body': 'A new deal has been created: {deal_title}'
        }
    },
    'deal_updated': {
        'ar': {
            'title': 'تحديث صفقة',
            'body': 'تم تحديث معلومات الصفقة: {deal_title}'
        },
        'en': {
            'title': 'Deal Updated',
            'body': 'Deal has been updated: {deal_title}'
        }
    },
    'deal_closed': {
        'ar': {
            'title': 'إغلاق صفقة',
            'body': 'تم إغلاق الصفقة {deal_title} بقيمة {value}'
        },
        'en': {
            'title': 'Deal Closed',
            'body': 'Deal {deal_title} has been closed with value {value}'
        }
    },
    'deal_reminder': {
        'ar': {
            'title': 'تذكير صفقة',
            'body': 'تذكير بموعد متابعة الصفقة: {deal_title}'
        },
        'en': {
            'title': 'Deal Reminder',
            'body': 'Reminder to follow up on deal: {deal_title}'
        }
    },
    'daily_report': {
        'ar': {
            'title': 'تقرير يومي',
            'body': 'اليوم: {leads_count} عميل محتمل – {deals_count} مبيعات'
        },
        'en': {
            'title': 'Daily Report',
            'body': 'Today: {leads_count} leads – {deals_count} sales'
        }
    },
    'weekly_report': {
        'ar': {
            'title': 'تقرير أسبوعي',
            'body': 'تقرير الأداء الأسبوعي جاهز'
        },
        'en': {
            'title': 'Weekly Report',
            'body': 'Weekly performance report is ready'
        }
    },
    'top_employee': {
        'ar': {
            'title': 'أفضل موظف',
            'body': 'أفضل موظف مبيعات لهذا الأسبوع: {employee_name}'
        },
        'en': {
            'title': 'Top Employee',
            'body': 'Top sales employee this week: {employee_name}'
        }
    },
    'login_from_new_device': {
        'ar': {
            'title': 'تسجيل دخول جديد',
            'body': 'تم تسجيل دخول من جهاز جديد: {device}'
        },
        'en': {
            'title': 'Login from New Device',
            'body': 'Login detected from new device: {device}'
        }
    },
    'system_update': {
        'ar': {
            'title': 'تحديث النظام',
            'body': 'تم إضافة ميزة جديدة إلى Loop CRM: {feature}'
        },
        'en': {
            'title': 'System Update',
            'body': 'New feature added to Loop CRM: {feature}'
        }
    },
    'subscription_expiring': {
        'ar': {
            'title': 'تنبيه الاشتراك',
            'body': 'اشتراكك ينتهي خلال {days_remaining} أيام'
        },
        'en': {
            'title': 'Subscription Expiring',
            'body': 'Your subscription expires in {days_remaining} days'
        }
    },
    'payment_failed': {
        'ar': {
            'title': 'فشل الدفع',
            'body': 'فشل عملية الدفع، يرجى التحقق'
        },
        'en': {
            'title': 'Payment Failed',
            'body': 'Payment failed, please check'
        }
    },
    'subscription_expired': {
        'ar': {
            'title': 'انتهاء الاشتراك',
            'body': 'انتهى الاشتراك، يرجى التجديد'
        },
        'en': {
            'title': 'Subscription Expired',
            'body': 'Subscription has expired, please renew'
        }
    },
    'general': {
        'ar': {
            'title': 'إشعار عام',
            'body': 'هذا إشعار عام'
        },
        'en': {
            'title': 'General Notification',
            'body': 'This is a general notification'
        }
    },
}


def get_notification_text(notification_type: str, language: str = 'ar', **kwargs) -> Dict[str, str]:
    """
    Get notification title and body in the specified language
    
    Args:
        notification_type: Type of notification (e.g., 'new_lead')
        language: Language code ('ar' or 'en')
        **kwargs: Additional data to format the message (e.g., campaign_name, lead_name)
    
    Returns:
        Dict with 'title' and 'body' keys
    """
    # Default to Arabic if language not supported
    if language not in ['ar', 'en']:
        language = 'ar'
    
    # Get translations for this notification type
    translations = NOTIFICATION_TRANSLATIONS.get(notification_type, {})
    
    # Get translation for the requested language, fallback to Arabic
    lang_translations = translations.get(language, translations.get('ar', {
        'title': 'Notification',
        'body': 'You have a new notification'
    }))
    
    # Format the message with provided kwargs
    title = lang_translations.get('title', 'Notification')
    body = lang_translations.get('body', 'You have a new notification')
    
    try:
        title = title.format(**kwargs)
        body = body.format(**kwargs)
    except KeyError:
        # If formatting fails (missing keys), return as is
        pass
    
    return {
        'title': title,
        'body': body
    }
