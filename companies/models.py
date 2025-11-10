from django.db import models


class Company(models.Model):
    name = models.CharField(max_length=64)
    domain = models.CharField(max_length=256)
    owner = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="companies"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "companies"

    def __str__(self):
        return self.name
