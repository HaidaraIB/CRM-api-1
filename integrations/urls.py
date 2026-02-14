from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    IntegrationAccountViewSet,
    IntegrationLogViewSet,
    meta_webhook,
    tiktok_webhook,
    tiktok_leadgen_webhook,
)
from .whatsapp_webhook import whatsapp_webhook

router = DefaultRouter()
router.register(r'accounts', IntegrationAccountViewSet, basename='integration-account')
router.register(r'logs', IntegrationLogViewSet, basename='integration-log')

urlpatterns = [
    path('', include(router.urls)),
    # Webhook endpoints (must be before router to avoid conflicts)
    path('webhooks/meta/', meta_webhook, name='meta_webhook'),
    path('webhooks/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
    path('webhooks/tiktok/', tiktok_webhook, name='tiktok_webhook'),
    path('webhooks/tiktok-leadgen/', tiktok_leadgen_webhook, name='tiktok_leadgen_webhook'),
]

