import json
from pathlib import Path

from django.core.management.base import BaseCommand

from validation.catalog import build_vectors, catalog_document


class Command(BaseCommand):
    help = "Write validation catalog.json / vectors.json for web and mobile clients."

    def handle(self, *args, **options):
        api_root = Path(__file__).resolve().parents[3]
        workspace = api_root.parent
        document = catalog_document()
        vectors = {"vectors": build_vectors()}
        targets = [
            (workspace / "CRM-project" / "forms" / "catalog" / "catalog.snapshot.json", document),
            (workspace / "CRM-project" / "forms" / "catalog" / "vectors.json", vectors),
            (workspace / "CRM-admin-panel" / "forms" / "catalog" / "catalog.snapshot.json", document),
            (workspace / "CRM-admin-panel" / "forms" / "catalog" / "vectors.json", vectors),
            (workspace / "crm_mobile" / "assets" / "validation" / "catalog.json", document),
            (workspace / "crm_mobile" / "test" / "forms" / "vectors.json", vectors),
        ]
        for path, payload in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.stdout.write(f"Wrote {path}")
