"""
Refresh Meta/WhatsApp long-lived tokens and notify owners when tokens are invalid.

Usage:
    python manage.py refresh_integration_tokens
    python manage.py refresh_integration_tokens --dry-run
"""
from django.core.management.base import BaseCommand

from integrations.tasks import refresh_expired_tokens, validate_meta_tokens


class Command(BaseCommand):
    help = "Refresh integration tokens nearing expiry and validate Meta tokens"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report counts without refreshing or notifying",
        )
        parser.add_argument(
            "--validate-only",
            action="store_true",
            help="Only run Meta debug_token validation (no proactive refresh)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        validate_only = options.get("validate_only", False)

        if dry_run:
            from django.utils import timezone
            from datetime import timedelta
            from integrations.models import IntegrationAccount
            from integrations.services.token_lifecycle import REFRESH_BEFORE_EXPIRY

            threshold = timezone.now() + REFRESH_BEFORE_EXPIRY
            due = IntegrationAccount.objects.filter(
                status="connected",
                token_expires_at__lte=threshold,
                token_expires_at__isnull=False,
                is_active=True,
            ).count()
            meta_connected = IntegrationAccount.objects.filter(
                platform="meta",
                status="connected",
                is_active=True,
            ).count()
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would refresh ~{due} account(s); "
                    f"would validate {meta_connected} connected Meta account(s)."
                )
            )
            return

        if validate_only:
            result = validate_meta_tokens()
        else:
            result = refresh_expired_tokens()

        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
