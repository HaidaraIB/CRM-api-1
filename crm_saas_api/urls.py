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


# Import viewsets
from accounts.views import UserViewSet, CustomTokenObtainPairView, register_company
from companies.views import CompanyViewSet
from crm.views import ClientViewSet, DealViewSet, TaskViewSet, CampaignViewSet, ClientTaskViewSet
from settings.views import ChannelViewSet, LeadStageViewSet, LeadStatusViewSet, SMTPSettingsViewSet
from real_estate.views import DeveloperViewSet, ProjectViewSet, UnitViewSet, OwnerViewSet
from services.views import ServiceViewSet, ServicePackageViewSet, ServiceProviderViewSet
from products.views import ProductViewSet, ProductCategoryViewSet, SupplierViewSet
from subscriptions.views import (
    PlanViewSet, SubscriptionViewSet, PaymentViewSet, 
    InvoiceViewSet, BroadcastViewSet, PaymentGatewayViewSet
)

# Create a router and register viewsets
router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"companies", CompanyViewSet, basename="company")
router.register(r"clients", ClientViewSet, basename="client")
router.register(r"deals", DealViewSet, basename="deal")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"client-tasks", ClientTaskViewSet, basename="clienttask")
router.register(r"campaigns", CampaignViewSet, basename="campaign")
router.register(r"settings/channels", ChannelViewSet, basename="channel")
router.register(r"settings/stages", LeadStageViewSet, basename="leadstage")
router.register(r"settings/statuses", LeadStatusViewSet, basename="leadstatus")
router.register(r"settings/smtp", SMTPSettingsViewSet, basename="smtpsettings")
router.register(r"developers", DeveloperViewSet, basename="developer")
router.register(r"projects", ProjectViewSet, basename="project")
router.register(r"units", UnitViewSet, basename="unit")
router.register(r"owners", OwnerViewSet, basename="owner")
router.register(r"services", ServiceViewSet, basename="service")
router.register(r"service-packages", ServicePackageViewSet, basename="servicepackage")
router.register(r"service-providers", ServiceProviderViewSet, basename="serviceprovider")
router.register(r"products", ProductViewSet, basename="product")
router.register(r"product-categories", ProductCategoryViewSet, basename="productcategory")
router.register(r"suppliers", SupplierViewSet, basename="supplier")
router.register(r"plans", PlanViewSet, basename="plan")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscription")
router.register(r"payments", PaymentViewSet, basename="payment")
router.register(r"invoices", InvoiceViewSet, basename="invoice")
router.register(r"broadcasts", BroadcastViewSet, basename="broadcast")
router.register(r"payment-gateways", PaymentGatewayViewSet, basename="paymentgateway")

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    # JWT Authentication endpoints
    path(
        "api/auth/login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"
    ),
    path("api/auth/register/", register_company, name="register_company"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
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
