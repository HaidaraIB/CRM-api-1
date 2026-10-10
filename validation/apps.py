from django.apps import AppConfig


class ValidationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "validation"
    verbose_name = "Form validation"

    def ready(self):
        from validation.schemas import load_all
        from validation.bindings import install

        load_all()
        install()
