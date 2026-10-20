"""Object keys: every tenant-owned upload lives under its company's prefix."""

from __future__ import annotations

import posixpath
from datetime import datetime

from django.utils.deconstruct import deconstructible

PLATFORM_PREFIX = "platform"


def company_prefix(company_id) -> str:
    """``companies/<id>``; objects with no company (e.g. a platform admin's photo) go under ``platform``."""
    return f"companies/{int(company_id)}" if company_id else PLATFORM_PREFIX


@deconstructible
class TenantUploadTo:
    """
    ``upload_to`` that prefixes the company: ``companies/<id>/<folder>/<filename>``.

    ``company`` is a dotted attribute path from the instance to the company id,
    e.g. ``"company_id"`` or ``"conversation.company_id"``. ``folder`` may use
    ``strftime`` codes, like a plain ``upload_to`` string.
    """

    def __init__(self, folder: str, company: str = "company_id"):
        self.folder = folder
        self.company = company

    def company_id(self, instance):
        value = instance
        for part in self.company.split("."):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value

    def __call__(self, instance, filename: str) -> str:
        folder = datetime.now().strftime(self.folder).strip("/")
        return posixpath.join(company_prefix(self.company_id(instance)), folder, filename)

    def __eq__(self, other):
        return isinstance(other, TenantUploadTo) and (self.folder, self.company) == (other.folder, other.company)

    def __hash__(self):
        return hash((self.folder, self.company))
