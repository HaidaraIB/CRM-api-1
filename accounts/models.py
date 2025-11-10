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
    role = models.CharField(max_length=64, choices=Role.choices)

    def __str__(self):
        return self.username

    class Meta:
        db_table = "users"
