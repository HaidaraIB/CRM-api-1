from django.apps import AppConfig


class TenantChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenant_chat"
    verbose_name = "Tenant internal chat"

    def ready(self):
        import tenant_chat.signals  # noqa: F401
