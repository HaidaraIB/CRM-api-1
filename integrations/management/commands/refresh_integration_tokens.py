"""
Refresh Meta/WhatsApp long-lived tokens and notify owners when tokens are invalid.

Usage:
    python manage.py refresh_integration_tokens
    python manage.py refresh_integration_tokens --dry-run
    python manage.py refresh_integration_tokens --validate-only
"""
from django.core.management.base import BaseCommand

from integrations.services.token_lifecycle import (
    accounts_due_for_refresh,
    refresh_expired_tokens,
    refreshable_accounts,
    validate_meta_tokens,
)


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
            # Counted through the same querysets the real run uses, so the dry run
            # cannot claim a different scope than the thing it is previewing.
            due = accounts_due_for_refresh().count()
            to_validate = refreshable_accounts().count()
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would refresh ~{due} account(s); "
                    f"would validate {to_validate} connected account(s)."
                )
            )
            return

        if validate_only:
            result = validate_meta_tokens()
        else:
            result = refresh_expired_tokens()

        self.stdout.write(self.style.SUCCESS(f"Done: {result}"))
