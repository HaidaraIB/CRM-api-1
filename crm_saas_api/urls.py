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
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)


# Import viewsets
from accounts.views import UserViewSet
from companies.views import CompanyViewSet
from crm.views import ClientViewSet, DealViewSet, TaskViewSet
from subscriptions.views import PlanViewSet, SubscriptionViewSet, PaymentViewSet

# Create a router and register viewsets
router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"companies", CompanyViewSet, basename="company")
router.register(r"clients", ClientViewSet, basename="client")
router.register(r"deals", DealViewSet, basename="deal")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"plans", PlanViewSet, basename="plan")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscription")
router.register(r"payments", PaymentViewSet, basename="payment")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api-auth/", include("rest_framework.urls")),  # For browsable API login
]
