from django.conf import settings
from django.db import models


class CompanyLibraryFile(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        DOCUMENT = "document", "Document"

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="library_files",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="library_uploads",
    )
    file = models.FileField(upload_to="company_library/%Y/%m/%d/", max_length=500)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.original_filename} (company={self.company_id})"
