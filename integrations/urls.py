from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    IntegrationAccountViewSet,
    IntegrationLogViewSet,
    MessageTemplateViewSet,
    meta_webhook,
    tiktok_leadgen_webhook,
    whatsapp_send_message,
    whatsapp_send_template,
    whatsapp_session_window,
    whatsapp_conversations_list,
    whatsapp_limits,
    twilio_settings_view,
    send_lead_sms_view,
    LeadSMSMessageViewSet,
    LeadWhatsAppMessageViewSet,
    integration_policy_view,
    openai_settings_view,
    openai_settings_test_view,
    ai_insights_list_view,
    ai_insights_dashboard_view,
    ai_insights_run_view,
    ai_insight_approve_view,
    ai_insight_dismiss_view,
)
from .whatsapp_webhook import whatsapp_webhook

router = DefaultRouter()
router.register(r'accounts', IntegrationAccountViewSet, basename='integration-account')
router.register(r'logs', IntegrationLogViewSet, basename='integration-log')
router.register(r'sms', LeadSMSMessageViewSet, basename='lead-sms-message')
router.register(r'whatsapp/messages', LeadWhatsAppMessageViewSet, basename='lead-whatsapp-message')
router.register(r'templates', MessageTemplateViewSet, basename='message-template')

urlpatterns = [
    path('', include(router.urls)),
    path('whatsapp/send/', whatsapp_send_message, name='whatsapp_send'),
    path('whatsapp/send-template/', whatsapp_send_template, name='whatsapp_send_template'),
    path('whatsapp/session-window/', whatsapp_session_window, name='whatsapp_session_window'),
    path('policy/', integration_policy_view, name='integration_policy'),
    path('whatsapp/limits/', whatsapp_limits, name='whatsapp_limits'),
    path('whatsapp/conversations/', whatsapp_conversations_list, name='whatsapp_conversations'),
    path('webhooks/meta/', meta_webhook, name='meta_webhook'),
    path('webhooks/whatsapp/', whatsapp_webhook, name='whatsapp_webhook'),
    path('webhooks/tiktok-leadgen/', tiktok_leadgen_webhook, name='tiktok_leadgen_webhook'),
    path('twilio/settings/', twilio_settings_view, name='twilio_settings'),
    path('twilio/send/', send_lead_sms_view, name='send_lead_sms'),
    path('openai/settings/', openai_settings_view, name='openai_settings'),
    path('openai/settings/test/', openai_settings_test_view, name='openai_settings_test'),
    path('ai-insights/', ai_insights_list_view, name='ai_insights_list'),
    path('ai-insights/dashboard/', ai_insights_dashboard_view, name='ai_insights_dashboard'),
    path('ai-insights/run/', ai_insights_run_view, name='ai_insights_run'),
    path('ai-insights/<int:pk>/approve/', ai_insight_approve_view, name='ai_insight_approve'),
    path('ai-insights/<int:pk>/dismiss/', ai_insight_dismiss_view, name='ai_insight_dismiss'),
]

