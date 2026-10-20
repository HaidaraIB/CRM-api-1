from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

tenant_router = DefaultRouter()
tenant_router.register(r"extensions", views.PbxExtensionViewSet, basename="pbx-extension")
tenant_router.register(r"trunks", views.PbxTrunkViewSet, basename="pbx-trunk")
tenant_router.register(r"numbers", views.PbxNumberViewSet, basename="pbx-number")
tenant_router.register(r"inbound-routes", views.PbxInboundRouteViewSet, basename="pbx-inbound-route")
tenant_router.register(r"outbound-routes", views.PbxOutboundRouteViewSet, basename="pbx-outbound-route")
tenant_router.register(r"business-hours", views.PbxBusinessHoursViewSet, basename="pbx-business-hours")
tenant_router.register(r"ivr-menus", views.PbxIvrMenuViewSet, basename="pbx-ivr-menu")
tenant_router.register(r"queues", views.PbxQueueViewSet, basename="pbx-queue")
tenant_router.register(r"sounds", views.PbxSoundViewSet, basename="pbx-sound")
tenant_router.register(r"gateways", views.PbxGatewayViewSet, basename="pbx-gateway")
tenant_router.register(r"calls", views.PbxCallViewSet, basename="pbx-call")
tenant_router.register(r"events", views.PbxEventReplayViewSet, basename="pbx-event")

admin_router = DefaultRouter()
admin_router.register(r"nodes", views.AdminPbxNodeViewSet, basename="pbx-admin-node")
admin_router.register(r"tenants", views.AdminPbxTenantViewSet, basename="pbx-admin-tenant")
admin_router.register(r"numbers", views.AdminPbxNumberViewSet, basename="pbx-admin-number")
admin_router.register(r"fraud-alerts", views.AdminFraudAlertViewSet, basename="pbx-admin-fraud-alert")

tenant_urlpatterns = [
    path("status/", views.TelephonyStatusView.as_view(), name="pbx-status"),
    path("settings/", views.TelephonySettingsView.as_view(), name="pbx-settings"),
    path("me/", views.TelephonyMeView.as_view(), name="pbx-me"),
    path("me/webrtc-credentials/", views.WebRtcCredentialsView.as_view(), name="pbx-me-webrtc-credentials"),
    path("agents/status/", views.AgentPresenceView.as_view(), name="pbx-agents-status"),
    path("agents/me/status/", views.MyAgentStatusView.as_view(), name="pbx-agents-me-status"),
    path("wallboard/", views.WallboardView.as_view(), name="pbx-wallboard"),
    path("recordings/<uuid:pk>/access/", views.PbxRecordingAccessView.as_view(), name="pbx-recording-access"),
    path("recordings/<uuid:pk>/", views.PbxRecordingDeleteView.as_view(), name="pbx-recording-delete"),
    path("usage/", views.TelephonyUsageView.as_view(), name="pbx-usage"),
    path("", include(tenant_router.urls)),
]

admin_urlpatterns = [
    path("numbers/<uuid:pk>/verify/", views.AdminVerifyNumberView.as_view(), name="pbx-admin-number-verify"),
    path("health/", views.AdminTelephonyHealthView.as_view(), name="pbx-admin-health"),
    path("", include(admin_router.urls)),
]

internal_urlpatterns = [
    path("events", views.InternalEventsView.as_view(), name="pbx-internal-events"),
    path("recordings", views.InternalRecordingsView.as_view(), name="pbx-internal-recordings"),
    path("nodes/<uuid:node_id>/desired-state", views.InternalDesiredStateView.as_view(), name="pbx-internal-desired-state"),
    path("nodes/<uuid:node_id>/apply-result", views.InternalApplyResultView.as_view(), name="pbx-internal-apply-result"),
    path(
        "nodes/<uuid:node_id>/artifacts/<str:tag>/<int:version>/",
        views.InternalArtifactView.as_view(),
        name="pbx-internal-artifact",
    ),
]

# Under /api/public/ (no X-API-Key, no JWT): the signed token is the authorization.
public_urlpatterns = [
    path("recordings/<str:token>/", views.recording_stream, name="pbx-recording-stream"),
    path("gateways/enroll/", views.GatewayEnrollView.as_view(), name="pbx-gateway-enroll"),
]
