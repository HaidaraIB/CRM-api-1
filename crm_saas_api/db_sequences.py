"""Helpers for Postgres identity / serial PK sequences."""

from __future__ import annotations

from django.db import connection


def reset_pk_sequence(model) -> bool:
    """
    Align Postgres PK sequence with MAX(pk) for ``model``.

    Returns True if a sequence was updated. No-op on non-Postgres backends.
    Safe when the table is empty (next nextval yields 1).
    """
    if connection.vendor != "postgresql":
        return False

    table = model._meta.db_table
    pk_col = model._meta.pk.column
    qn = connection.ops.quote_name

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_serial_sequence(%s, %s)",
            [table, pk_col],
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return False

        sequence_name = row[0]
        cursor.execute(
            f"SELECT setval(%s, COALESCE((SELECT MAX({qn(pk_col)}) FROM {qn(table)}), 1))",
            [sequence_name],
        )
    return True


def reset_pk_sequences(models) -> list[str]:
    """Reset PK sequences for each model; returns db table names that were updated."""
    updated: list[str] = []
    for model in models:
        if reset_pk_sequence(model):
            updated.append(model._meta.db_table)
    return updated
