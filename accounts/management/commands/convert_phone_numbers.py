"""
Convert stored phones to +E.164. Previews unless --apply is given.

Only numbers the audit classifies as safe are touched. Invalid values, non-phones
and users sharing a number in different formats are reported and left alone.
Rows change through queryset.update(), so no signals or notifications fire.

    python manage.py convert_phone_numbers
    python manage.py convert_phone_numbers --include-local-07
    python manage.py convert_phone_numbers --apply --undo-file phones_undo.json
    python manage.py convert_phone_numbers --restore phones_undo.json
"""

import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from accounts.models import User
from crm.models import Client, ClientPhoneNumber
from integrations.services.phone_match import canonical_phone_key
from validation.phones import is_dialable, to_e164, to_wire_digits

from .audit_phone_numbers import SOURCES, classify

MODELS = {label: (model, column) for label, model, column in SOURCES}


def duplicate_user_ids() -> set[int]:
    seen: dict[str, list[int]] = {}
    users = User.objects.exclude(phone__isnull=True).exclude(phone="")
    for user_id, phone in users.values_list("id", "phone"):
        if is_dialable(phone):
            seen.setdefault(to_wire_digits(phone), []).append(user_id)
    return {user_id for ids in seen.values() if len(ids) > 1 for user_id in ids}


def changes_for(model, new_value: str) -> dict:
    column = "phone" if model not in (Client, ClientPhoneNumber) else "phone_number"
    values = {column: new_value}
    if model is ClientPhoneNumber:
        values["phone_normalized"] = canonical_phone_key(new_value) or ""
    return values


class Command(BaseCommand):
    help = "Convert stored phones to +E.164. Dry run unless --apply is given."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the changes.")
        parser.add_argument("--undo-file", help="Where --apply records old values. Required with --apply.")
        parser.add_argument(
            "--include-local-07",
            action="store_true",
            help="Also convert local 07… numbers, assuming they are Iraqi.",
        )
        parser.add_argument("--restore", help="Put back the old values from an undo file.")

    def handle(self, *args, **options):
        if options["restore"]:
            self._restore(options["restore"])
            return
        if options["apply"] and not options["undo_file"]:
            raise CommandError("--apply needs --undo-file.")

        kinds = {"convertible"} | ({"local_07"} if options["include_local_07"] else set())
        held_users = duplicate_user_ids()
        undo = []
        for label, model, column in SOURCES:
            rows = list(
                model.objects.exclude(**{f"{column}__isnull": True})
                .exclude(**{column: ""})
                .values_list("pk", column)
            )
            counts = Counter()
            for pk, value in rows:
                kind = classify(value)
                if kind not in kinds:
                    counts[f"left_{kind}"] += 1
                elif model is User and pk in held_users:
                    counts["held_duplicate_user"] += 1
                elif not options["apply"]:
                    counts["would_convert"] += 1
                elif self._convert(model, pk, column, value):
                    undo.append({"source": label, "pk": pk, "old": value})
                    counts["converted"] += 1
                else:
                    counts["conflict_skipped"] += 1
                    self.stdout.write(f"    {label} pk={pk}: conflict, left unchanged")
            self.stdout.write(f"{label}: {dict(counts)}")

        if options["apply"]:
            with open(options["undo_file"], "w", encoding="utf8") as handle:
                json.dump(undo, handle)
            self.stdout.write(f"Converted {len(undo)} rows. Undo file holds real numbers; delete it when done.")
        else:
            self.stdout.write("Dry run: nothing was written.")

    def _convert(self, model, pk, column, value) -> bool:
        new_value = to_e164(value)
        spellings = [new_value, to_wire_digits(value)]
        if model is User and User.objects.exclude(pk=pk).filter(phone__in=spellings).exists():
            return False
        try:
            with transaction.atomic():
                model.objects.filter(pk=pk, **{column: value}).update(**changes_for(model, new_value))
        except IntegrityError:
            return False
        return True

    def _restore(self, path):
        with open(path, encoding="utf8") as handle:
            undo = json.load(handle)
        for row in undo:
            model, column = MODELS[row["source"]]
            values = {column: row["old"]}
            if model is ClientPhoneNumber:
                values["phone_normalized"] = canonical_phone_key(row["old"]) or ""
            model.objects.filter(pk=row["pk"]).update(**values)
        self.stdout.write(f"Restored {len(undo)} rows.")
