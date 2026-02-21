from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    IntegrationAccountViewSet,
    IntegrationLogViewSet,
    MessageTemplateViewSet,
    meta_webhook,
    tiktok_leadgen_webhook,
    whatsapp_send_message,
    twilio_settings_view,
    send_lead_sms_view,
    LeadSMSMessageViewSet,
)
from .whatsapp_webhook import whatsapp_webhook

router = DefaultRouter()
router.register(r'accounts', IntegrationAccountViewSet, basename='integration-account')
router.register(r'logs', IntegrationLogViewSet, basename='integration-log')
router.register(r'sms', LeadSMSMessageViewSet, basename='lead-sms-message')
router.register(r'templates', MessageTemplateViewSet, basename='message-template')

urlpatterns = [
    path('', include(router.urls)),
    path('whatsapp/send/', whatsapp_send_message, name='whatsapp_send'),
    path('webhooks/meta/', meta_webhook, name='meta_webhook'),
    path('webhooks/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
    path('webhooks/tiktok-leadgen/', tiktok_leadgen_webhook, name='tiktok_leadgen_webhook'),
    path('twilio/settings/', twilio_settings_view, name='twilio_settings'),
    path('twilio/send/', send_lead_sms_view, name='send_lead_sms'),
]

