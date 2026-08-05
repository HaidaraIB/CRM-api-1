"""Tests for company file library API."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status

from company_library.models import CompanyLibraryFile
from conftest import api_body


def _pdf_upload(name="brochure.pdf", size=1024):
    return SimpleUploadedFile(
        name,
        b"%PDF-1.4\n" + (b"x" * max(0, size - 9)),
        content_type="application/pdf",
    )


@pytest.mark.django_db
class TestCompanyLibrary:
    def test_admin_can_upload_and_list(self, authenticated_admin, admin_user, company):
        resp = authenticated_admin.post(
            "/api/company-library/",
            {"file": _pdf_upload()},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.content
        data = api_body(resp)
        assert data["file"]["original_filename"] == "brochure.pdf"
        assert data["file"]["kind"] == "document"
        assert data["quota"]["file_count"] == 1
        assert data["quota"]["used_bytes"] > 0

        list_resp = authenticated_admin.get("/api/company-library/")
        assert list_resp.status_code == status.HTTP_200_OK
        list_data = api_body(list_resp)
        results = list_data.get("results") or list_data
        if isinstance(results, dict) and "results" in results:
            results = results["results"]
        assert len(results) >= 1
        assert list_data.get("quota") or (isinstance(list_data, dict) and "quota" in str(list_data))

    def test_employee_can_list_and_download_but_not_upload(
        self, api_client, employee_user, admin_user, subscription, company
    ):
        api_client.force_authenticate(user=admin_user)
        upload = api_client.post(
            "/api/company-library/",
            {"file": _pdf_upload("shared.pdf")},
            format="multipart",
        )
        assert upload.status_code == status.HTTP_201_CREATED
        file_id = api_body(upload)["file"]["id"]

        api_client.force_authenticate(user=employee_user)
        denied = api_client.post(
            "/api/company-library/",
            {"file": _pdf_upload("emp.pdf")},
            format="multipart",
        )
        assert denied.status_code == status.HTTP_403_FORBIDDEN

        listed = api_client.get("/api/company-library/")
        assert listed.status_code == status.HTTP_200_OK

        dl = api_client.get(f"/api/company-library/{file_id}/download/")
        assert dl.status_code == status.HTTP_200_OK
        assert dl["Content-Type"].startswith("application/pdf")

    def test_rejects_file_over_plan_max_size(self, authenticated_admin, plan, company):
        plan.limits = {**(plan.limits or {}), "max_file_size_bytes": 100}
        plan.save(update_fields=["limits"])

        resp = authenticated_admin.post(
            "/api/company-library/",
            {"file": _pdf_upload(size=500)},
            format="multipart",
        )
        assert resp.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN)

    def test_rejects_when_storage_full(self, authenticated_admin, plan, company, admin_user):
        plan.limits = {
            **(plan.limits or {}),
            "max_storage_bytes": 200,
            "max_file_size_bytes": 10_000,
        }
        plan.save(update_fields=["limits"])

        first = authenticated_admin.post(
            "/api/company-library/",
            {"file": _pdf_upload("a.pdf", size=150)},
            format="multipart",
        )
        assert first.status_code == status.HTTP_201_CREATED

        second = authenticated_admin.post(
            "/api/company-library/",
            {"file": _pdf_upload("b.pdf", size=100)},
            format="multipart",
        )
        assert second.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN)

    def test_tenant_isolation(
        self, authenticated_admin, other_admin_user, other_company, api_client, plan
    ):
        from subscriptions.models import BillingCycle, Subscription
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.now()
        Subscription.objects.create(
            company=other_company,
            plan=plan,
            is_active=True,
            start_date=now,
            end_date=now + timedelta(days=30),
            current_period_start=now,
            billing_cycle=BillingCycle.MONTHLY,
        )

        upload = authenticated_admin.post(
            "/api/company-library/",
            {"file": _pdf_upload("mine.pdf")},
            format="multipart",
        )
        assert upload.status_code == status.HTTP_201_CREATED
        file_id = api_body(upload)["file"]["id"]

        api_client.force_authenticate(user=other_admin_user)
        listed = api_client.get("/api/company-library/")
        assert listed.status_code == status.HTTP_200_OK
        body = api_body(listed)
        results = body.get("results", body)
        if isinstance(results, dict):
            results = results.get("results", [])
        ids = [r["id"] for r in results]
        assert file_id not in ids

        dl = api_client.get(f"/api/company-library/{file_id}/download/")
        assert dl.status_code == status.HTTP_404_NOT_FOUND

    def test_admin_can_rename_and_delete(self, authenticated_admin, company):
        upload = authenticated_admin.post(
            "/api/company-library/",
            {"file": _pdf_upload("old.pdf")},
            format="multipart",
        )
        file_id = api_body(upload)["file"]["id"]

        renamed = authenticated_admin.patch(
            f"/api/company-library/{file_id}/",
            {"original_filename": "new-name.pdf"},
            format="json",
        )
        assert renamed.status_code == status.HTTP_200_OK
        assert api_body(renamed)["original_filename"] == "new-name.pdf"

        deleted = authenticated_admin.delete(f"/api/company-library/{file_id}/")
        assert deleted.status_code == status.HTTP_200_OK
        assert not CompanyLibraryFile.objects.filter(pk=file_id).exists()
