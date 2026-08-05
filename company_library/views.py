from django.db.models import Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import validation_error_response
from subscriptions.entitlements import (
    build_company_entitlements,
    require_quota,
    resolve_quota_limit,
)

from .models import CompanyLibraryFile
from .permissions import IsCompanyLibraryAdmin, IsCompanyLibraryUser
from .serializers import CompanyLibraryFileRenameSerializer, CompanyLibraryFileSerializer
from .validation import validate_library_upload


def _company_storage_used(company_id: int) -> int:
    total = (
        CompanyLibraryFile.objects.filter(company_id=company_id)
        .aggregate(total=Sum("size_bytes"))
        .get("total")
    )
    return int(total or 0)


def _quota_summary(company) -> dict:
    ent = build_company_entitlements(company)
    used = _company_storage_used(company.id)
    max_storage = resolve_quota_limit(ent, "max_storage_bytes")
    max_file_size = resolve_quota_limit(ent, "max_file_size_bytes")
    return {
        "used_bytes": used,
        "max_storage_bytes": max_storage,
        "max_file_size_bytes": max_file_size,
        "remaining_bytes": None if max_storage is None else max(0, max_storage - used),
        "file_count": CompanyLibraryFile.objects.filter(company_id=company.id).count(),
    }


class CompanyLibraryFileViewSet(viewsets.ModelViewSet):
    """
    Company file library.
    - List/retrieve/download: all company users
    - Create/update/destroy: company admins only
    """

    serializer_class = CompanyLibraryFileSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ("create", "partial_update", "update", "destroy"):
            return [IsAuthenticated(), HasActiveSubscription(), IsCompanyLibraryAdmin()]
        return [IsAuthenticated(), HasActiveSubscription(), IsCompanyLibraryUser()]

    def get_queryset(self):
        user = self.request.user
        return (
            CompanyLibraryFile.objects.filter(company_id=user.company_id)
            .select_related("uploaded_by")
            .order_by("-created_at")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        payload = {
            "results": serializer.data if page is None else serializer.data,
            "quota": _quota_summary(request.user.company),
        }
        if page is not None:
            # Keep paginator envelope but attach quota on the page response data
            response = self.get_paginated_response(serializer.data)
            data = response.data
            if isinstance(data, dict):
                data["quota"] = payload["quota"]
            return response
        return Response(payload)

    def create(self, request, *args, **kwargs):
        uploaded = request.FILES.get("file")
        if not uploaded:
            return validation_error_response({"file": ["This field is required."]})

        try:
            kind, mime_type, size_bytes, filename = validate_library_upload(uploaded)
        except ValueError as exc:
            return validation_error_response({"file": [str(exc)]})

        company = request.user.company
        require_quota(
            company,
            "max_file_size_bytes",
            current_count=0,
            requested_delta=size_bytes,
            message="This file exceeds your plan's maximum file size.",
            error_key="library_file_too_large",
        )
        require_quota(
            company,
            "max_storage_bytes",
            current_count=_company_storage_used(company.id),
            requested_delta=size_bytes,
            message="Uploading this file would exceed your plan's library storage limit.",
            error_key="library_storage_exceeded",
        )

        obj = CompanyLibraryFile(
            company=company,
            uploaded_by=request.user,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            kind=kind,
        )
        obj.file.save(filename, uploaded, save=False)
        obj.save()

        out = self.get_serializer(obj)
        return Response(
            {"file": out.data, "quota": _quota_summary(company)},
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        ser = CompanyLibraryFileRenameSerializer(data=request.data)
        if not ser.is_valid():
            return validation_error_response(ser.errors)
        instance.original_filename = ser.validated_data["original_filename"]
        instance.save(update_fields=["original_filename", "updated_at"])
        return Response(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        company = request.user.company
        if instance.file:
            instance.file.delete(save=False)
        instance.delete()
        return Response({"quota": _quota_summary(company)}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="quota")
    def quota(self, request):
        return Response(_quota_summary(request.user.company))


class CompanyLibraryFileDownloadView(APIView):
    """Authenticated download for a library file (not a public /media URL)."""

    permission_classes = [
        IsAuthenticated,
        HasActiveSubscription,
        IsCompanyLibraryUser,
    ]

    def get(self, request, pk):
        obj = get_object_or_404(
            CompanyLibraryFile.objects.filter(company_id=request.user.company_id),
            pk=pk,
        )
        if not obj.file:
            return Response(status=status.HTTP_404_NOT_FOUND)

        stream = obj.file.open("rb")
        resp = FileResponse(
            stream,
            as_attachment=True,
            filename=obj.original_filename or "file",
            content_type=obj.mime_type or "application/octet-stream",
        )
        resp["Cache-Control"] = "private, max-age=3600"
        return resp
