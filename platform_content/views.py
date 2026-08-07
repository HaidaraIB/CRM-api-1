from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import CanManageContent, HasActiveSubscription
from crm_saas_api.responses import error_response, success_response

from .models import GuideArticle, NewsPost, UserNewsReadState
from .notify import NOTIFY_CHANNELS, notify_company_owners_news_async
from .serializers import (
    GuideArticleListSerializer,
    GuideArticleSerializer,
    NewsPostListSerializer,
    NewsPostSerializer,
)


class GuideArticleAdminViewSet(viewsets.ModelViewSet):
    """Super admin / limited admin CRUD for guide articles (including drafts)."""

    queryset = GuideArticle.objects.all()
    permission_classes = [IsAuthenticated, CanManageContent]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return GuideArticleListSerializer
        return GuideArticleSerializer


class NewsPostAdminViewSet(viewsets.ModelViewSet):
    """Super admin / limited admin CRUD for news posts (including drafts)."""

    queryset = NewsPost.objects.all()
    permission_classes = [IsAuthenticated, CanManageContent]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return NewsPostListSerializer
        return NewsPostSerializer

    @action(detail=True, methods=["post"], url_path="notify")
    def notify(self, request, pk=None):
        """
        Manually notify company owners about this published news post.

        Body: { "channels": "push" | "email" | "both" }
        """
        news = self.get_object()
        if not news.is_published:
            return error_response(
                "Only published news can be notified.",
                code="news_not_published",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        channels = (request.data.get("channels") or "both").strip().lower()
        if channels not in NOTIFY_CHANNELS:
            return error_response(
                "Invalid channels. Use push, email, or both.",
                code="invalid_notify_channels",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        news.notified_at = now
        news.last_notify_channels = channels
        news.save(update_fields=["notified_at", "last_notify_channels", "updated_at"])

        notify_company_owners_news_async(news.pk, channels=channels)

        serializer = NewsPostSerializer(news, context={"request": request})
        return success_response(
            data={
                **serializer.data,
                "notify_queued": True,
                "channels": channels,
            },
            message="Owner notifications queued.",
            status_code=status.HTTP_200_OK,
        )


class PublishedGuideArticleViewSet(viewsets.ReadOnlyModelViewSet):
    """Tenant read-only access to published guide articles."""

    permission_classes = [IsAuthenticated, HasActiveSubscription]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        return GuideArticle.objects.filter(is_published=True)

    def get_serializer_class(self):
        if self.action == "list":
            return GuideArticleListSerializer
        return GuideArticleSerializer


class PublishedNewsPostViewSet(viewsets.ReadOnlyModelViewSet):
    """Tenant read-only access to published news posts + unread helpers."""

    permission_classes = [IsAuthenticated, HasActiveSubscription]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return NewsPost.objects.filter(is_published=True)

    def get_serializer_class(self):
        if self.action == "list":
            return NewsPostListSerializer
        return NewsPostSerializer

    def _unread_queryset(self, user):
        qs = NewsPost.objects.filter(is_published=True, published_at__isnull=False)
        try:
            state = user.news_read_state
            return qs.filter(published_at__gt=state.last_read_at)
        except UserNewsReadState.DoesNotExist:
            return qs

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = self._unread_queryset(request.user).count()
        return success_response(data={"unread_count": count})

    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_read(self, request):
        """Mark all published news as read for the current user (sidebar badge)."""
        now = timezone.now()
        UserNewsReadState.objects.update_or_create(
            user=request.user,
            defaults={"last_read_at": now},
        )
        return success_response(
            data={"unread_count": 0, "last_read_at": now.isoformat()},
            status_code=status.HTTP_200_OK,
        )
