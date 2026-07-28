"""
Reset Postgres PK sequences that fell behind MAX(id).

Typical after fixture loads or inserts with explicit primary keys. Without this,
registration fails when seeding default channels/stages with:
  IntegrityError: duplicate key value violates unique constraint "..._pkey"

Usage:
    python manage.py fix_pk_sequences
    python manage.py fix_pk_sequences --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import connection

from crm_saas_api.db_sequences import reset_pk_sequence
from settings.models import CallMethod, Channel, LeadStage, LeadStatus, VisitType


# Tables most likely to break company registration seeding.
DEFAULT_MODELS = (Channel, LeadStage, LeadStatus, CallMethod, VisitType)


class Command(BaseCommand):
    help = "Reset Postgres PK sequences to MAX(id) for settings seed tables (and optionally more)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show current max(id) vs sequence last_value without changing anything",
        )
        parser.add_argument(
            "--all-apps",
            action="store_true",
            help="Reset sequences for every model with an AutoField/BigAutoField PK",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stderr.write(self.style.ERROR("This command only applies to PostgreSQL."))
            return

        models = list(DEFAULT_MODELS)
        if options["all_apps"]:
            from django.apps import apps

            models = []
            for model in apps.get_models():
                pk = model._meta.pk
                if pk is None:
                    continue
                if getattr(pk, "auto_created", False) or pk.get_internal_type() in (
                    "AutoField",
                    "BigAutoField",
                ):
                    models.append(model)

        dry_run = options["dry_run"]
        for model in models:
            table = model._meta.db_table
            pk_col = model._meta.pk.column
            qn = connection.ops.quote_name
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)",
                    [table, pk_col],
                )
                seq_row = cursor.fetchone()
                if not seq_row or not seq_row[0]:
                    self.stdout.write(f"{table}: no sequence (skipped)")
                    continue
                sequence_name = seq_row[0]
                cursor.execute(f"SELECT COALESCE(MAX({qn(pk_col)}), 0) FROM {qn(table)}")
                max_id = cursor.fetchone()[0] or 0
                cursor.execute(
                    f"SELECT last_value, is_called FROM {sequence_name}"
                )
                last_value, is_called = cursor.fetchone()
                next_id = last_value + 1 if is_called else last_value
                status = "ok" if next_id > max_id or max_id == 0 else "BEHIND"
                self.stdout.write(
                    f"{table}: max_id={max_id} sequence_next={next_id} [{status}]"
                )
                if dry_run or status == "ok":
                    continue
                reset_pk_sequence(model)
                self.stdout.write(self.style.SUCCESS(f"  -> reset {sequence_name} to {max_id}"))

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run only; no sequences were changed."))
