from django.db import models
from django.contrib.auth.models import AbstractUser
from enum import Enum


class Role(Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    EMPLOYEE = "employee"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class User(AbstractUser):
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, null=True, blank=True
    )
    role = models.CharField(max_length=64, choices=Role.choices())
    phone = models.CharField(max_length=20, blank=True, null=True)

    def __str__(self):
        return self.username

    def is_super_admin(self):
        return self.role == Role.SUPER_ADMIN.value

    def is_admin(self):
        return self.role == Role.ADMIN.value

    def is_employee(self):
        return self.role == Role.EMPLOYEE.value

    def has_role(self, role):
        return self.role == role

    def can_access_user(self, user):
        if self == user:
            return True
        if self.is_admin() and self.company == user.company:
            return True
        return False

    def can_access_company_data(self, company):
        if self.is_super_admin():
            return True
        return self.company == company

    class Meta:
        db_table = "users"
        permissions = [
            ("view_all_users", "Can view all users"),
            ("manage_all_users", "Can manage all users"),
            ("manage_company_users", "Can manage company users"),
            ("view_company_data", "Can view company data"),
            ("manage_company_data", "Can manage company data"),
        ]
