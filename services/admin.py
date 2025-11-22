from django.contrib import admin
from .models import ServiceProvider, Service, ServicePackage


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "category", "price", "is_active", "company", "created_at"]
    list_filter = ["company", "category", "is_active", "created_at"]
    search_fields = ["name", "code", "category"]


@admin.register(ServicePackage)
class ServicePackageAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "price", "company", "created_at"]
    list_filter = ["company", "created_at"]
    search_fields = ["name", "code"]
    filter_horizontal = ["services"]


@admin.register(ServiceProvider)
class ServiceProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "phone", "company", "created_at"]
    list_filter = ["company", "created_at"]
    search_fields = ["name", "code", "phone"]

