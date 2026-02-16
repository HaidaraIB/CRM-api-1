from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    IntegrationAccountViewSet,
    IntegrationLogViewSet,
    meta_webhook,
    tiktok_leadgen_webhook,
    whatsapp_send_message,
)
from .whatsapp_webhook import whatsapp_webhook

router = DefaultRouter()
router.register(r'accounts', IntegrationAccountViewSet, basename='integration-account')
router.register(r'logs', IntegrationLogViewSet, basename='integration-log')

urlpatterns = [
    path('', include(router.urls)),
    path('whatsapp/send/', whatsapp_send_message, name='whatsapp_send'),
    path('webhooks/meta/', meta_webhook, name='meta_webhook'),
    path('webhooks/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
    path('webhooks/tiktok-leadgen/', tiktok_leadgen_webhook, name='tiktok_leadgen_webhook'),
]

