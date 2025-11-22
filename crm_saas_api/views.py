from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema


class HomeResponseSerializer(serializers.Serializer):
    """Serializer for the home page response"""
    message = serializers.CharField()
    welcome = serializers.CharField()
    version = serializers.CharField()
    description = serializers.CharField()
    endpoints = serializers.DictField()
    authentication = serializers.DictField()
    user_roles = serializers.DictField()
    features = serializers.ListField(child=serializers.CharField())


@extend_schema(
    summary="Home Page",
    description="Home Page for the API - Displays information about the API and available endpoints",
    responses={200: HomeResponseSerializer},
    tags=["General"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def home(request):
    """
    Home Page for the API - Displays information about the API and available endpoints
    """
    base_url = request.build_absolute_uri("/")
    
    data = {
        "message": "Welcome to LOOP CRM API",
        "welcome": "Welcome to LOOP CRM API",
        "version": "1.0.0",
        "description": "Multi-company CRM system for managing customer relationships",
        "endpoints": {
            "authentication": {
                "login": f"{base_url}api/auth/login/",
                "refresh": f"{base_url}api/auth/refresh/",
                "verify": f"{base_url}api/auth/verify/",
            },
            "api": {
                "users": f"{base_url}api/users/",
                "companies": f"{base_url}api/companies/",
                "clients": f"{base_url}api/clients/",
                "deals": f"{base_url}api/deals/",
                "tasks": f"{base_url}api/tasks/",
                "plans": f"{base_url}api/plans/",
                "subscriptions": f"{base_url}api/subscriptions/",
                "payments": f"{base_url}api/payments/",
            },
            "documentation": {
                "swagger": f"{base_url}api/docs/",
                "redoc": f"{base_url}api/redoc/",
                "schema": f"{base_url}api/schema/",
            },
            "admin": {
                "admin_panel": f"{base_url}admin/",
            },
        },
        "authentication": {
            "type": "JWT (JSON Web Token)",
            "header": "Authorization: Bearer <token>",
            "access_token_lifetime": "1 hour",
            "refresh_token_lifetime": "7 days",
        },
        "user_roles": {
            "super_admin": "Can access all data",
            "admin": "Can manage only his company",
            "employee": "Can access only his data",
        },
        "features": [
            "User and company management",
            "Client and deal management",
            "Task and reminder management",
            "Subscription and payment management",
            "Secure JWT authentication",
            "Multiple level permissions",
            "Comprehensive API documentation (Swagger/ReDoc)",
        ],
    }
    
    return Response(data)
