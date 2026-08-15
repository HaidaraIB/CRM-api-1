from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'

    def ready(self):
        import subscriptions.signals  # noqa: F401
        from subscriptions.gateways import autodiscover

        # Import the gateway adapters so they register themselves.
        autodiscover()
