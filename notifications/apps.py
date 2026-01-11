from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    
    def ready(self):
        """Initialize Firebase when app is ready"""
        from .services import NotificationService
        NotificationService.initialize()
