from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from crm_saas_api.responses import error_response, success_response, validation_error_response

from accounts.models import User
from accounts.permissions import CanManageTenants
from accounts.platform_whatsapp import (
    normalize_phone_digits,
    platform_whatsapp_configured,
    send_admin_message,
)
from .models import AdminTenantWhatsAppMessage, Company
from .serializers import (
    AdminTenantWhatsAppMessageSerializer,
    CompanyListSerializer,
    CompanySerializer,
)


class CompanyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Company instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = Company.objects.select_related("owner").all()
    permission_classes = [IsAuthenticated, CanManageTenants]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "domain", "owner__username"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        """Create company and automatically link owner's company field"""
        company = serializer.save()
        
        # تحديث company field في User (owner) تلقائياً
        if company.owner:
            company.owner.company = company
            company.owner.save(update_fields=['company'])
    
    def perform_update(self, serializer):
        """Update company and handle owner changes"""
        old_owner = None
        if self.get_object():
            old_owner = self.get_object().owner
        
        company = serializer.save()
        new_owner = company.owner
        
        # إذا تغير owner، تحديث company field في User الجديد
        if new_owner and new_owner != old_owner:
            new_owner.company = company
            new_owner.save(update_fields=['company'])
            
            # إزالة company من owner القديم (إن وجد)
            if old_owner and old_owner != new_owner:
                old_owner.company = None
                old_owner.save(update_fields=['company'])
        elif company.owner:
            # تأكد من أن owner مرتبط بالـ company
            if company.owner.company != company:
                company.owner.company = company
                company.owner.save(update_fields=['company'])

    def get_serializer_class(self):
        if self.action == "list":
            return CompanyListSerializer
        return CompanySerializer

    @action(detail=True, methods=["post"], url_path="admin-whatsapp/send")
    def admin_whatsapp_send(self, request, pk=None):
        """
        POST /api/companies/{id}/admin-whatsapp/send/
        Body: { "message": "..." }
        """
        company = self.get_object()
        body = (request.data.get("message") or request.data.get("body") or "").strip()
        if not body:
            return validation_error_response({"message": ["Message is required."]})
        if not platform_whatsapp_configured():
            return error_response(
                "Platform WhatsApp is not configured.",
                code="platform_whatsapp_not_configured",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        to_digits = normalize_phone_digits(getattr(company.owner, "phone", None) or "")
        if not to_digits:
            return error_response(
                "Company owner has no phone number.",
                code="owner_phone_missing",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        ok, details = send_admin_message(to_digits, body)
        wam_id = None
        graph_status = None
        if isinstance(details, dict):
            msgs = details.get("messages")
            if isinstance(msgs, list) and msgs:
                wam_id = msgs[0].get("id")
            graph_status = details.get("graph_http_status")
        if not ok:
            return error_response(
                "Failed to send WhatsApp message.",
                code="whatsapp_send_failed",
                status_code=status.HTTP_502_BAD_GATEWAY,
                details=details if isinstance(details, dict) else {"error": str(details)},
            )
        AdminTenantWhatsAppMessage.objects.create(
            company=company,
            direction=AdminTenantWhatsAppMessage.DIRECTION_OUTBOUND,
            body=body[:65535],
            whatsapp_message_id=wam_id,
            graph_http_status=graph_status,
        )
        return success_response(data={"whatsapp_message_id": wam_id})

    @action(detail=True, methods=["get"], url_path="admin-whatsapp/messages")
    def admin_whatsapp_messages(self, request, pk=None):
        """GET /api/companies/{id}/admin-whatsapp/messages/?page=1&page_size=50"""
        company = self.get_object()
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(200, max(1, int(request.query_params.get("page_size", 50))))
        except (TypeError, ValueError):
            page, page_size = 1, 50
        qs = AdminTenantWhatsAppMessage.objects.filter(company=company).order_by("created_at")
        total = qs.count()
        start = (page - 1) * page_size
        rows = qs[start : start + page_size]
        ser = AdminTenantWhatsAppMessageSerializer(rows, many=True)
        return success_response(
            data={
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": ser.data,
            }
        )

    @action(detail=True, methods=['patch'], permission_classes=[IsAuthenticated])
    def update_assignment_settings(self, request, pk=None):
        """
        Update auto assign and re-assign settings for the company
        PATCH /api/companies/{id}/update_assignment_settings/
        Body: {
            "auto_assign_enabled": true/false,
            "re_assign_enabled": true/false,
            "re_assign_hours": 24
        }
        """
        company = self.get_object()
        user = request.user
        
        # Check permissions: user must be admin of this company or super admin
        if not (user.is_super_admin() or (user.is_admin() and user.company == company)):
            return error_response(
                "You do not have permission to update these settings.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        
        # Update settings
        auto_assign_enabled = request.data.get('auto_assign_enabled')
        re_assign_enabled = request.data.get('re_assign_enabled')
        re_assign_hours = request.data.get('re_assign_hours')
        
        if auto_assign_enabled is not None:
            company.auto_assign_enabled = bool(auto_assign_enabled)
        if re_assign_enabled is not None:
            company.re_assign_enabled = bool(re_assign_enabled)
        if re_assign_hours is not None:
            try:
                hours = int(re_assign_hours)
                if hours < 1:
                    return error_response(
                        "re_assign_hours must be at least 1 hour.",
                        code="invalid_re_assign_hours",
                    )
                company.re_assign_hours = hours
            except (ValueError, TypeError):
                return error_response(
                    "re_assign_hours must be a valid integer.",
                    code="invalid_re_assign_hours",
                )
        
        company.save(update_fields=['auto_assign_enabled', 're_assign_enabled', 're_assign_hours'])
        
        serializer = CompanySerializer(company, context={'request': request})
        return success_response(data=serializer.data)
