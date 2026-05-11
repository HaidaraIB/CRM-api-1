"""
URL configuration for crm_saas_api project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from .views import home
from accounts.views import check_registration_availability

# Import viewsets
from accounts.views import (
    UserViewSet,
    LimitedAdminViewSet,
    SupervisorViewSet,
    CustomTokenObtainPairView,
    register_company,
    phone_otp_requirement_settings,
    registration_email_requirement_settings,
    register_phone_send_otp,
    register_phone_verify_otp,
    register_email_send_otp,
    register_email_verify_otp,
    impersonate,
    impersonate_exchange,
    impersonate_exchange_status,
    verify_email,
    resend_verification,
    forgot_password,
    reset_password,
    request_two_factor_auth,
    verify_two_factor_auth,
    pre_login_email_resend,
    pre_login_email_change,
    pre_login_phone_send_otp,
    pre_login_phone_verify_otp,
    pre_login_phone_change,
    update_fcm_token,
    remove_fcm_token,
    remove_fcm_token_device,
    update_language,
    fcm_diagnostics_full,
)
from companies.views import CompanyViewSet
from crm.views import (
    ClientViewSet,
    DealViewSet,
    TaskViewSet,
    CampaignViewSet,
    ClientTaskViewSet,
    ClientCallViewSet,
    ClientVisitViewSet,
    ClientEventViewSet,
)
from settings.views import (
    ChannelViewSet,
    LeadStageViewSet,
    LeadStatusViewSet,
    CallMethodViewSet,
    VisitTypeViewSet,
    SystemBackupViewSet,
    SystemAuditLogViewSet,
    SystemSettingsViewSet,
    PlatformTwilioSettingsViewSet,
    PlatformWhatsAppSettingsViewSet,
    BillingSettingsViewSet,
)
from settings.views_public import MobileAppVersionPublicView
from real_estate.views import (
    DeveloperViewSet,
    ProjectViewSet,
    UnitViewSet,
    OwnerViewSet,
)
from services.views import ServiceViewSet, ServicePackageViewSet, ServiceProviderViewSet
from products.views import ProductViewSet, ProductCategoryViewSet, SupplierViewSet
from subscriptions.views import (
    PlanViewSet,
    SubscriptionViewSet,
    PaymentViewSet,
    InvoiceViewSet,
    BroadcastViewSet,
    PaymentGatewayViewSet,
    PublicPlanListView,
    PublicPaymentGatewayListView,
    create_paytabs_payment,
    paytabs_return,
    create_zaincash_payment,
    zaincash_return,
    create_stripe_payment,
    stripe_return,
    create_qicard_payment,
    qicard_return,
    qicard_webhook,
    create_fib_payment,
    fib_callback,
    check_payment_status,
    preview_subscription_change,
    schedule_subscription_downgrade,
    switch_subscription_plan_free,
)
from integrations import urls as integrations_urls
from support.views import SupportTicketViewSet
from tenant_chat.views import TenantChatConversationViewSet, TenantChatMessageAttachmentView

# Create a router and register viewsets
router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"companies", CompanyViewSet, basename="company")

router.register(r"clients", ClientViewSet, basename="client")
router.register(r"client-tasks", ClientTaskViewSet, basename="clienttask")
router.register(r"client-calls", ClientCallViewSet, basename="clientcall")
router.register(r"client-visits", ClientVisitViewSet, basename="clientvisit")
router.register(r"client-events", ClientEventViewSet, basename="clientevent")

router.register(r"deals", DealViewSet, basename="deal")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"campaigns", CampaignViewSet, basename="campaign")

router.register(r"settings/channels", ChannelViewSet, basename="channel")
router.register(r"settings/stages", LeadStageViewSet, basename="leadstage")
router.register(r"settings/statuses", LeadStatusViewSet, basename="leadstatus")
router.register(r"settings/call-methods", CallMethodViewSet, basename="callmethod")
router.register(r"settings/visit-types", VisitTypeViewSet, basename="visittype")
router.register(r"settings/backups", SystemBackupViewSet, basename="systembackup")
router.register(
    r"settings/audit-logs", SystemAuditLogViewSet, basename="systemauditlog"
)
router.register(
    r"settings/system", SystemSettingsViewSet, basename="systemsettings"
)
router.register(
    r"settings/platform-twilio", PlatformTwilioSettingsViewSet, basename="platformtwiliosettings"
)
router.register(
    r"settings/platform-whatsapp",
    PlatformWhatsAppSettingsViewSet,
    basename="platformwhatsappsettings",
)
router.register(
    r"settings/billing", BillingSettingsViewSet, basename="billingsettings"
)

router.register(r"developers", DeveloperViewSet, basename="developer")
router.register(r"projects", ProjectViewSet, basename="project")
router.register(r"units", UnitViewSet, basename="unit")
router.register(r"owners", OwnerViewSet, basename="owner")

router.register(r"services", ServiceViewSet, basename="service")
router.register(r"service-packages", ServicePackageViewSet, basename="servicepackage")
router.register(
    r"service-providers", ServiceProviderViewSet, basename="serviceprovider"
)

router.register(r"products", ProductViewSet, basename="product")
router.register(
    r"product-categories", ProductCategoryViewSet, basename="productcategory"
)
router.register(r"suppliers", SupplierViewSet, basename="supplier")

router.register(r"plans", PlanViewSet, basename="plan")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscription")
router.register(r"payments", PaymentViewSet, basename="payment")
router.register(r"invoices", InvoiceViewSet, basename="invoice")
router.register(r"payment-gateways", PaymentGatewayViewSet, basename="paymentgateway")

router.register(r"broadcasts", BroadcastViewSet, basename="broadcast")
router.register(r"limited-admins", LimitedAdminViewSet, basename="limitedadmin")
router.register(r"supervisors", SupervisorViewSet, basename="supervisor")
router.register(r"support-tickets", SupportTicketViewSet, basename="supportticket")
router.register(
    r"tenant-chat/conversations",
    TenantChatConversationViewSet,
    basename="tenant_chat_conversation",
)

# --- Versioned API patterns (v1) ---
v1_patterns = [
    path("payments/create-paytabs-session/", create_paytabs_payment, name="create_paytabs_payment"),
    path("payments/paytabs-return/", paytabs_return, name="paytabs_return"),
    path("payments/create-zaincash-session/", create_zaincash_payment, name="create_zaincash_payment"),
    path("payments/zaincash-return/", zaincash_return, name="zaincash_return"),
    path("payments/create-stripe-session/", create_stripe_payment, name="create_stripe_payment"),
    path("payments/stripe-return/", stripe_return, name="stripe_return"),
    path("payments/create-qicard-session/", create_qicard_payment, name="create_qicard_payment"),
    path("payments/qicard-return/", qicard_return, name="qicard_return"),
    path("payments/qicard-webhook/", qicard_webhook, name="qicard_webhook"),
    path("payments/create-fib-session/", create_fib_payment, name="create_fib_payment"),
    path("payments/fib-callback/", fib_callback, name="fib_callback"),
    path("payment-status/<int:subscription_id>/", check_payment_status, name="check_payment_status"),
    path("subscriptions/switch-plan-free/", switch_subscription_plan_free, name="switch_subscription_plan_free"),
    path("subscriptions/preview-change/", preview_subscription_change, name="preview_subscription_change"),
    path("subscriptions/schedule-downgrade/", schedule_subscription_downgrade, name="schedule_subscription_downgrade"),
    path("users/update-fcm-token/", update_fcm_token, name="update_fcm_token"),
    path("users/remove-fcm-token/", remove_fcm_token, name="remove_fcm_token"),
    path(
        "users/remove-fcm-token-device/",
        remove_fcm_token_device,
        name="remove_fcm_token_device",
    ),
    path("users/update-language/", update_language, name="update_language"),
    path("users/fcm-diagnostics-full/", fcm_diagnostics_full, name="fcm_diagnostics_full"),
    path(
        "tenant-chat/messages/<int:pk>/attachment/",
        TenantChatMessageAttachmentView.as_view(),
        name="tenant_chat_message_attachment",
    ),
    path("", include(router.urls)),
    path("auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/register/", register_company, name="register_company"),
    path(
        "auth/register/phone-otp-requirement/",
        phone_otp_requirement_settings,
        name="phone_otp_requirement_settings",
    ),
    path(
        "auth/register/email-verification-requirement/",
        registration_email_requirement_settings,
        name="registration_email_requirement_settings",
    ),
    path("auth/register/phone/send-otp/", register_phone_send_otp, name="register_phone_send_otp"),
    path("auth/register/phone/verify-otp/", register_phone_verify_otp, name="register_phone_verify_otp"),
    path("auth/register/email/send-otp/", register_email_send_otp, name="register_email_send_otp"),
    path("auth/register/email/verify-otp/", register_email_verify_otp, name="register_email_verify_otp"),
    path("auth/impersonate/", impersonate, name="impersonate"),
    path("auth/impersonate-exchange/status/", impersonate_exchange_status, name="impersonate_exchange_status"),
    path("auth/impersonate-exchange/", impersonate_exchange, name="impersonate_exchange"),
    re_path(r"^auth/impersonate-exchange$", impersonate_exchange, name="impersonate_exchange_no_slash"),
    path("auth/check-availability/", check_registration_availability, name="check_registration_availability"),
    path("auth/verify-email/", verify_email, name="verify_email"),
    path("auth/pre-login/email/resend/", pre_login_email_resend, name="pre_login_email_resend"),
    path("auth/pre-login/email/change/", pre_login_email_change, name="pre_login_email_change"),
    path("auth/pre-login/phone/send-otp/", pre_login_phone_send_otp, name="pre_login_phone_send_otp"),
    path("auth/pre-login/phone/verify-otp/", pre_login_phone_verify_otp, name="pre_login_phone_verify_otp"),
    path("auth/pre-login/phone/change/", pre_login_phone_change, name="pre_login_phone_change"),
    path("auth/resend-verification/", resend_verification, name="resend_verification"),
    path("auth/forgot-password/", forgot_password, name="forgot_password"),
    path("auth/reset-password/", reset_password, name="reset_password"),
    path("auth/request-2fa/", request_two_factor_auth, name="request_two_factor_auth"),
    path("auth/verify-2fa/", verify_two_factor_auth, name="verify_two_factor_auth"),
    path("public/plans/", PublicPlanListView.as_view(), name="public_plan_list"),
    path("public/payment-gateways/", PublicPaymentGatewayListView.as_view(), name="public_payment_gateway_list"),
    path(
        "public/mobile-app-version/",
        MobileAppVersionPublicView.as_view(),
        name="public_mobile_app_version",
    ),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("integrations/", include(integrations_urls)),
    path("", include("notifications.urls")),
]

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    # Versioned API (canonical)
    path("api/v1/", include((v1_patterns, "v1"))),
    # Backward-compatible unversioned routes (same patterns, no namespace)
    path("api/", include(v1_patterns)),
    # API Documentation
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api-auth/", include("rest_framework.urls")),
]

# Serve static files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
