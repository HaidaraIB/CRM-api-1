from django.apps import AppConfig


class ObjectStorageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "object_storage"
    verbose_name = "Object storage (Cloudflare R2)"
