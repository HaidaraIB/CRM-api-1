from django.db import models
from enum import Enum


class ChannelPriority(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class Channel(models.Model):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=100)
    priority = models.CharField(max_length=10, choices=ChannelPriority.choices(), default=ChannelPriority.MEDIUM.value)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="channels"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['name', 'company']

    def __str__(self):
        return self.name


class LeadStage(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#808080')  # Hex color
    required = models.BooleanField(default=False)
    auto_advance = models.BooleanField(default=False)
    order = models.IntegerField(default=0)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="lead_stages"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        unique_together = ['name', 'company']

    def __str__(self):
        return self.name


class StatusCategory(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FOLLOW_UP = "follow_up"
    CLOSED = "closed"

    @classmethod
    def choices(cls):
        return [(choice.value, choice.name) for choice in cls]


class LeadStatus(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=20, choices=StatusCategory.choices(), default=StatusCategory.ACTIVE.value)
    color = models.CharField(max_length=7, default='#808080')  # Hex color
    is_default = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    company = models.ForeignKey(
        "companies.Company", on_delete=models.CASCADE, related_name="lead_statuses"
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', 'name']
        unique_together = ['name', 'company']

    def __str__(self):
        return self.name


