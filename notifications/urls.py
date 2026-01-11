from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, send_notification, notification_settings

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    path('notifications/send/', send_notification, name='send_notification'),
    path('notifications/settings/', notification_settings, name='notification_settings'),
] + router.urls
