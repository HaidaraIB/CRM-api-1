"""Promotion bar: Super Admin CRUD + the public "what's live right now" read."""

from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import CanManagePlans

from ..models import Promotion, PromotionAudience
from ..serializers import PromotionSerializer, PublicPromotionSerializer

PROMOTION_STATUSES = ("live", "scheduled", "ended", "paused")


class PromotionViewSet(viewsets.ModelViewSet):
    serializer_class = PromotionSerializer
    permission_classes = [IsAuthenticated, CanManagePlans]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["starts_at", "ends_at", "priority", "created_at"]
    ordering = ["-starts_at", "-pk"]
    pagination_class = None

    def get_queryset(self):
        qs = Promotion.objects.all()
        wanted = (self.request.query_params.get("status") or "").strip()
        if wanted in PROMOTION_STATUSES:
            now = timezone.now()
            ids = [p.pk for p in qs if p.status_at(now) == wanted]
            qs = qs.filter(pk__in=ids)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        source = self.get_object()
        copy = Promotion.objects.get(pk=source.pk)
        copy.pk = None
        copy.name = f"{source.name} (copy)"[:120]
        # A copy starts paused so it never goes live by accident.
        copy.is_active = False
        copy.created_by = request.user
        copy.save()
        return Response(self.get_serializer(copy).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([AllowAny])
def active_promotion_public(request):
    """
    GET public/promotions/active/?audience=public|dashboard|mobile

    Returns the single promotion that surface should show now, plus the server
    clock so clients can correct countdowns for device clock skew.
    """
    audience = (request.query_params.get("audience") or PromotionAudience.PUBLIC).strip()
    if audience not in {a.value for a in PromotionAudience}:
        audience = PromotionAudience.PUBLIC
    now = timezone.now()
    promo = Promotion.objects.current(at=now, audience=audience)
    response = Response(
        {
            "promotion": PublicPromotionSerializer(promo).data if promo else None,
            "server_time": now.isoformat(),
        }
    )
    # Never cache: `server_time` is the clients' clock-skew reference, and a cached copy
    # (browser or CDN) pairs an old server clock with a fresh device clock, which shifts
    # every countdown. It is one small query, so serving it fresh is cheap.
    response["Cache-Control"] = "no-store"
    return response
