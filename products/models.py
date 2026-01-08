from django.db import models


class ProductCategory(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    parent_category = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="subcategories",
        blank=True,
        null=True,
    )
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="product_categories"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_categories"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_product_category_code_per_company')
        ]

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    category = models.ForeignKey(
        ProductCategory, on_delete=models.CASCADE, related_name="products"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    stock = models.IntegerField(default=0)
    supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.SET_NULL,
        related_name="products",
        blank=True,
        null=True,
    )
    sku = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="products"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_product_code_per_company')
        ]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    specialization = models.CharField(max_length=255, blank=True, null=True)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="suppliers"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "suppliers"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=['code', 'company'], name='unique_supplier_code_per_company')
        ]

    def __str__(self):
        return self.name

