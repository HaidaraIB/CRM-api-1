from django.contrib import admin
from .models import Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """Admin configuration for Company model"""

    list_display = [
        "name",
        "domain",
        "owner",
        "created_at",
        "updated_at",
        "specialization",
    ]
    list_filter = [
        "created_at",
        "updated_at",
    ]
    search_fields = [
        "name",
        "domain",
        "owner__username",
        "owner__email",
    ]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Company Information", {"fields": ("name", "domain", "owner", "specialization")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        """Override save to automatically update owner's company field"""
        old_owner = None
        if change and obj.pk:
            try:
                old_company = Company.objects.get(pk=obj.pk)
                old_owner = old_company.owner
            except Company.DoesNotExist:
                pass
        
        # حفظ Company أولاً
        super().save_model(request, obj, form, change)
        
        # تحديث company field في User (owner) تلقائياً
        new_owner = obj.owner
        if new_owner:
            # تحديث company field في owner الجديد
            if new_owner.company != obj:
                new_owner.company = obj
                new_owner.save(update_fields=['company'])
        
        # إزالة company من owner القديم (إن وجد وتغير)
        if old_owner and old_owner != new_owner:
            # فقط إذا كان owner القديم مرتبط بهذه الشركة
            if old_owner.company == obj:
                old_owner.company = None
                old_owner.save(update_fields=['company'])