from django.db import models
from enum import Enum


class Developer(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="developers"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "developers"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_developer_code_per_company')
        ]

    def __str__(self):
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    developer = models.ForeignKey(
        Developer, on_delete=models.CASCADE, related_name="projects"
    )
    type = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=255, blank=True, null=True)
    payment_method = models.CharField(max_length=255, blank=True, null=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="projects"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_project_code_per_company')
        ]

    def __str__(self):
        return self.name


class Unit(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="units"
    )
    bedrooms = models.IntegerField(blank=True, null=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    bathrooms = models.IntegerField(blank=True, null=True)
    type = models.CharField(max_length=255, blank=True, null=True)
    finishing = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=255, blank=True, null=True)
    district = models.CharField(max_length=255, blank=True, null=True)
    zone = models.CharField(max_length=255, blank=True, null=True)
    is_sold = models.BooleanField(default=False)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="units"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "units"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_unit_code_per_company')
        ]

    def __str__(self):
        return self.name


class Owner(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    phone = models.CharField(max_length=20, blank=True, null=True)
    city = models.CharField(max_length=255, blank=True, null=True)
    district = models.CharField(max_length=255, blank=True, null=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="owners"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "owners"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_owner_code_per_company')
        ]

    def __str__(self):
        return self.name
