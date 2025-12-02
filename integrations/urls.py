from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import IntegrationAccountViewSet, IntegrationLogViewSet

router = DefaultRouter()
router.register(r'accounts', IntegrationAccountViewSet, basename='integration-account')
router.register(r'logs', IntegrationLogViewSet, basename='integration-log')

urlpatterns = [
    path('', include(router.urls)),
]

