from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, send_notification

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    path('notifications/send/', send_notification, name='send_notification'),
] + router.urls
