from django.db import models
from enum import Enum


class Specialization(Enum):
    REAL_ESTATE = "real_estate"
    SERVICES = "services"
    PRODUCTS = "products"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Company(models.Model):
    name = models.CharField(max_length=64)
    domain = models.CharField(max_length=256, unique=True)
    specialization = models.CharField(
        max_length=20, choices=Specialization.choices(), default=Specialization.REAL_ESTATE.value
    )
    owner = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="companies"
    )
    # Track if registration was completed (payment made)
    registration_completed = models.BooleanField(default=False)
    registration_completed_at = models.DateTimeField(null=True, blank=True)
    
    # Auto assignment settings
    auto_assign_enabled = models.BooleanField(
        default=False,
        help_text="توزيع العملاء على الموظفين حسب النشاط"
    )
    re_assign_enabled = models.BooleanField(
        default=False,
        help_text="تعيين موظف جديد للعميل في حال لم يتواصل معه الموظف الحالي خلال فترة محددة"
    )
    re_assign_hours = models.IntegerField(
        default=24,
        help_text="عدد الساعات قبل إعادة تعيين العميل (افتراضي: 24 ساعة)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "companies"

    def __str__(self):
        return self.name
