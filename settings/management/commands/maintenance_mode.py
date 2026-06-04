"""
Toggle platform maintenance mode from the shell.

Usage:
    python manage.py maintenance_mode --on --message "Upgrading database"
    python manage.py maintenance_mode --off
    python manage.py maintenance_mode --status
"""

from django.core.management.base import BaseCommand

from settings.maintenance_policy import (
    DEFAULT_MAINTENANCE_MESSAGE,
    get_maintenance_policy,
    invalidate_maintenance_cache,
)
from settings.models import SystemSettings


class Command(BaseCommand):
    help = "Enable, disable, or show platform maintenance mode"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--on", action="store_true", help="Enable maintenance mode")
        group.add_argument("--off", action="store_true", help="Disable maintenance mode")
        group.add_argument("--status", action="store_true", help="Show current status")
        parser.add_argument(
            "--message",
            type=str,
            default="",
            help="Custom message when using --on (optional)",
        )

    def handle(self, *args, **options):
        if options["status"]:
            policy = get_maintenance_policy()
            enabled = policy.get("enabled")
            self.stdout.write(
                self.style.WARNING("ON") if enabled else self.style.SUCCESS("OFF")
            )
            self.stdout.write(f"maintenance_mode: {enabled}")
            self.stdout.write(f"message: {policy.get('message')}")
            return

        settings = SystemSettings.get_settings()

        if options["on"]:
            settings.maintenance_mode = True
            if options["message"]:
                settings.maintenance_message = options["message"].strip()
            elif not (settings.maintenance_message or "").strip():
                settings.maintenance_message = DEFAULT_MAINTENANCE_MESSAGE
            settings.save(update_fields=["maintenance_mode", "maintenance_message", "updated_at"])
            invalidate_maintenance_cache()
            self.stdout.write(self.style.WARNING("Maintenance mode ENABLED"))
            self.stdout.write(f"message: {settings.maintenance_message}")
            return

        settings.maintenance_mode = False
        settings.save(update_fields=["maintenance_mode", "updated_at"])
        invalidate_maintenance_cache()
        self.stdout.write(self.style.SUCCESS("Maintenance mode DISABLED"))
