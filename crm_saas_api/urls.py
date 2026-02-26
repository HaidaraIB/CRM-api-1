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
from django.urls import path, include
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
    verify_email,
    resend_verification,
    forgot_password,
    reset_password,
    request_two_factor_auth,
    verify_two_factor_auth,
    update_fcm_token,
    update_language,
)
from companies.views import CompanyViewSet
from crm.views import (
    ClientViewSet,
    DealViewSet,
    TaskViewSet,
    CampaignViewSet,
    ClientTaskViewSet,
    ClientCallViewSet,
    ClientEventViewSet,
)
from settings.views import (
    ChannelViewSet,
    LeadStageViewSet,
    LeadStatusViewSet,
    CallMethodViewSet,
    SystemBackupViewSet,
    SystemAuditLogViewSet,
    SystemSettingsViewSet,
)
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
)
from integrations import urls as integrations_urls

# Create a router and register viewsets
router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"companies", CompanyViewSet, basename="company")

router.register(r"clients", ClientViewSet, basename="client")
router.register(r"client-tasks", ClientTaskViewSet, basename="clienttask")
router.register(r"client-calls", ClientCallViewSet, basename="clientcall")
router.register(r"client-events", ClientEventViewSet, basename="clientevent")

router.register(r"deals", DealViewSet, basename="deal")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"campaigns", CampaignViewSet, basename="campaign")

router.register(r"settings/channels", ChannelViewSet, basename="channel")
router.register(r"settings/stages", LeadStageViewSet, basename="leadstage")
router.register(r"settings/statuses", LeadStatusViewSet, basename="leadstatus")
router.register(r"settings/call-methods", CallMethodViewSet, basename="callmethod")
router.register(r"settings/backups", SystemBackupViewSet, basename="systembackup")
router.register(
    r"settings/audit-logs", SystemAuditLogViewSet, basename="systemauditlog"
)
router.register(
    r"settings/system", SystemSettingsViewSet, basename="systemsettings"
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

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    # Custom payment endpoints - must be before router.urls to avoid conflicts
    path(
        "api/payments/create-paytabs-session/",
        create_paytabs_payment,
        name="create_paytabs_payment",
    ),
    path("api/payments/paytabs-return/", paytabs_return, name="paytabs_return"),
    path(
        "api/payments/create-zaincash-session/",
        create_zaincash_payment,
        name="create_zaincash_payment",
    ),
    path("api/payments/zaincash-return/", zaincash_return, name="zaincash_return"),
    path(
        "api/payments/create-stripe-session/",
        create_stripe_payment,
        name="create_stripe_payment",
    ),
    path("api/payments/stripe-return/", stripe_return, name="stripe_return"),
    path(
        "api/payments/create-qicard-session/",
        create_qicard_payment,
        name="create_qicard_payment",
    ),
    path("api/payments/qicard-return/", qicard_return, name="qicard_return"),
    path("api/payments/qicard-webhook/", qicard_webhook, name="qicard_webhook"),
    path(
        "api/payments/create-fib-session/",
        create_fib_payment,
        name="create_fib_payment",
    ),
    path("api/payments/fib-callback/", fib_callback, name="fib_callback"),
    # Status endpoint - use a path that won't conflict with router
    path(
        "api/payment-status/<int:subscription_id>/",
        check_payment_status,
        name="check_payment_status",
    ),
    # Custom user endpoints - must be before router.urls to avoid conflicts
    path("api/users/update-fcm-token/", update_fcm_token, name="update_fcm_token"),
    path("api/users/update-language/", update_language, name="update_language"),
    # Router URLs (includes /api/payments/ which would conflict if placed before custom endpoints)
    path("api/", include(router.urls)),
    # JWT Authentication endpoints
    path(
        "api/auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"
    ),
    path("api/auth/register/", register_company, name="register_company"),
    path(
        "api/auth/check-availability/",
        check_registration_availability,
        name="check_registration_availability",
    ),
    path("api/auth/verify-email/", verify_email, name="verify_email"),
    path("api/auth/resend-verification/", resend_verification, name="resend_verification"),
    path("api/auth/forgot-password/", forgot_password, name="forgot_password"),
    path("api/auth/reset-password/", reset_password, name="reset_password"),
    path(
        "api/auth/request-2fa/", request_two_factor_auth, name="request_two_factor_auth"
    ),
    path("api/auth/verify-2fa/", verify_two_factor_auth, name="verify_two_factor_auth"),
    path("api/public/plans/", PublicPlanListView.as_view(), name="public_plan_list"),
    path("api/public/payment-gateways/", PublicPaymentGatewayListView.as_view(), name="public_payment_gateway_list"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
    # Integrations URLs
    path("api/integrations/", include(integrations_urls)),
    # Notifications URLs
    path("api/", include("notifications.urls")),
    # API Documentation
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api-auth/", include("rest_framework.urls")),  # For browsable API login
]

# Serve static files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
