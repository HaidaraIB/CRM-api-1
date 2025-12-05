from django.contrib import admin
from .models import Product, ProductCategory, Supplier


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "company", "created_at"]
    list_filter = ["company", "created_at"]
    search_fields = ["name", "code"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "category", "price", "is_active", "company", "created_at"]
    list_filter = ["company", "category", "is_active", "created_at"]
    search_fields = ["name", "code"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "phone", "company", "created_at"]
    list_filter = ["company", "created_at"]
    search_fields = ["name", "code", "phone"]

