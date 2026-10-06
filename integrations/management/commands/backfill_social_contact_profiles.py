"""
Backfill missing Instagram / Messenger display names via Graph profile enrichment.

Usage:
  .\\.venv\\Scripts\\python.exe manage.py backfill_social_contact_profiles
  .\\.venv\\Scripts\\python.exe manage.py backfill_social_contact_profiles --delay-ms 200 --limit 50
"""

from __future__ import annotations

import time
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Q

from integrations.models import SocialChannel, SocialContact
from integrations.services.meta_inbox_profile import ensure_contact_profile


class Command(BaseCommand):
    help = (
        "Enrich Instagram/Messenger SocialContact rows that lack a display name "
        "(Graph User Profile + avatar mirror)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delay-ms",
            type=int,
            default=200,
            help="Pause between Graph calls (default 200ms).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max contacts to process (0 = all).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-fetch even when a name already exists.",
        )

    def handle(self, *args, **options):
        delay_ms = max(0, int(options["delay_ms"] or 0))
        limit = max(0, int(options["limit"] or 0))
        force = bool(options["force"])

        qs = (
            SocialContact.objects.filter(
                channel__in=(SocialChannel.INSTAGRAM, SocialChannel.MESSENGER),
            )
            .select_related("connection", "company")
            .order_by("id")
        )
        if not force:
            qs = qs.filter(Q(name="") | Q(name__isnull=True))

        if limit:
            qs = qs[:limit]

        succeeded = 0
        failed = 0
        skipped = 0
        error_messages: Counter[str] = Counter()

        total = qs.count() if hasattr(qs, "count") else len(list(qs))
        self.stdout.write(f"Processing up to {total} contact(s)…")

        for contact in qs.iterator():
            connection = contact.connection
            if connection is None or not connection.get_page_access_token():
                skipped += 1
                error_messages["no_page_token_or_connection"] += 1
                continue

            before_name = (contact.name or "").strip()
            before_status = contact.profile_fetch_status

            try:
                ensure_contact_profile(contact, connection, force=True)
                contact.refresh_from_db()
            except Exception as exc:
                failed += 1
                error_messages[str(exc)[:120]] += 1
                self.stderr.write(
                    f"  error contact={contact.id} channel={contact.channel} "
                    f"external_id={contact.external_id}: {exc}"
                )
            else:
                after_name = (contact.name or "").strip()
                if contact.profile_fetch_status == "ok" and (after_name or contact.avatar_path):
                    succeeded += 1
                elif contact.profile_fetch_status == "failed":
                    failed += 1
                    error_messages[f"status=failed (was {before_status or 'empty'})"] += 1
                elif after_name and after_name != before_name:
                    succeeded += 1
                elif after_name:
                    skipped += 1
                    error_messages["already_named"] += 1
                else:
                    failed += 1
                    error_messages[
                        f"status={contact.profile_fetch_status or 'empty'}"
                    ] += 1

            if delay_ms:
                time.sleep(delay_ms / 1000.0)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"succeeded: {succeeded}"))
        self.stdout.write(f"failed:    {failed}")
        self.stdout.write(f"skipped:   {skipped}")
        if error_messages:
            self.stdout.write("most common errors / skip reasons:")
            for msg, count in error_messages.most_common(10):
                self.stdout.write(f"  {count:4d}  {msg}")
