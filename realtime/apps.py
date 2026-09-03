from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "realtime"
    verbose_name = "Realtime channel"

    def ready(self):
        """
        Subscribe to the sync counters.

        Registered unconditionally, not behind REALTIME_ENABLED: the flag is read
        at publish time so it can be toggled with override_settings in tests and
        without a restart in production. The listener is a no-op when it is off.
        """
        from sync.version import register_change_listener

        from .publish import on_counter_changed

        register_change_listener(on_counter_changed)
