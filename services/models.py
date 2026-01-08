from django.db import models


class ServiceProvider(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    specialization = models.CharField(max_length=255, blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, blank=True, null=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="service_providers"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_providers"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_service_provider_code_per_company')
        ]

    def __str__(self):
        return self.name


class Service(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    duration = models.CharField(max_length=100)
    provider = models.ForeignKey(
        ServiceProvider,
        on_delete=models.SET_NULL,
        related_name="services",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="services"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "services"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_service_code_per_company')
        ]

    def __str__(self):
        return self.name


class ServicePackage(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    duration = models.CharField(max_length=100, blank=True, null=True)
    services = models.ManyToManyField(Service, related_name="packages")
    is_active = models.BooleanField(default=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="service_packages"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_packages"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_service_package_code_per_company')
        ]

    def __str__(self):
        return self.name

