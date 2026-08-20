from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from crm_saas_api.responses import error_response, success_response, validation_error_response

from .models import Notification, NotificationType, NotificationSettings
from .serializers import (
    NotificationSerializer,
    NotificationListSerializer,
    NotificationSettingsSerializer,
)
from .services import NotificationService
from accounts.models import User
from sync.cache import invalidate_badges
import logging

logger = logging.getLogger(__name__)


def exclude_tenant_chat_push_notifications(queryset):
    """
    Tenant DM pushes use FCM only; legacy rows may still have data.kind=tenant_chat.

    Postgres and SQLite both treat missing JSON keys as SQL NULL. A bare
    ``exclude(data__kind=\"tenant_chat\")`` therefore drops *every* row without
    that key (WHERE NOT (kind = 'tenant_chat') is unknown for NULL).

    Only exclude rows that explicitly set kind=tenant_chat — works on both
    PostgreSQL (prod) and SQLite (local/tests).
    """
    return queryset.exclude(data__has_key="kind", data__kind="tenant_chat")


def exclude_inbox_noise_notifications(queryset):
    """
    Hide chat-channel noise from the bell inbox (handled in Chats / FCM instead).
    """
    qs = exclude_tenant_chat_push_notifications(queryset)
    return qs.exclude(type=NotificationType.WHATSAPP_MESSAGE_RECEIVED)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing and managing user notifications
    """
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        """Return notifications for the authenticated user (exclude soft-deleted)"""
        queryset = Notification.objects.filter(
            user=self.request.user,
            deleted_at__isnull=True,
        )

        read_status = self.request.query_params.get('read', None)
        if read_status is not None:
            read_status = read_status.lower() == 'true'
            queryset = queryset.filter(read=read_status)

        notification_type = self.request.query_params.get('type', None)
        if notification_type:
            queryset = queryset.filter(type=notification_type)

        return exclude_inbox_noise_notifications(queryset)

    def get_serializer_class(self):
        if self.action == 'list':
            return NotificationListSerializer
        return NotificationSerializer

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark a notification as read"""
        notification = self.get_object()
        notification.mark_as_read()
        invalidate_badges(request.user.id)
        return success_response(message="Notification marked as read")

    @action(detail=True, methods=['delete'], url_path='delete')
    def soft_delete(self, request, pk=None):
        """Soft-delete a single notification for the current user."""
        notification = self.get_object()
        if notification.deleted_at is None:
            notification.deleted_at = timezone.now()
            notification.save(update_fields=['deleted_at'])
            invalidate_badges(request.user.id)
        return success_response(message="Notification deleted")

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        """Mark all notifications as read (only non soft-deleted)"""
        count = exclude_inbox_noise_notifications(
            Notification.objects.filter(
                user=request.user,
                read=False,
                deleted_at__isnull=True,
            )
        ).update(read=True, read_at=timezone.now())
        invalidate_badges(request.user.id)

        return success_response(
            message=f"{count} notifications marked as read",
            data={"count": count},
        )

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """Get count of unread notifications (exclude soft-deleted)"""
        count = exclude_inbox_noise_notifications(
            Notification.objects.filter(
                user=request.user,
                read=False,
                deleted_at__isnull=True,
            )
        ).count()

        return success_response(data={"unread_count": count})

    @action(detail=False, methods=['delete'])
    def delete_all_read(self, request):
        """Soft-delete all read notifications (set deleted_at, do not remove from DB)"""
        qs = exclude_inbox_noise_notifications(
            Notification.objects.filter(
                user=request.user,
                read=True,
                deleted_at__isnull=True,
            )
        )
        count = qs.update(deleted_at=timezone.now())

        return success_response(
            message=f"{count} read notifications deleted",
            data={"count": count},
        )

    @action(detail=False, methods=['delete'], url_path='delete_all')
    def delete_all(self, request):
        """
        Soft-delete all notifications for the current user.

        Optional query/body ``type`` limits deletion to that notification type
        (e.g. ``pbx_incoming_call``). Comma-separated types are allowed.
        """
        qs = exclude_inbox_noise_notifications(
            Notification.objects.filter(
                user=request.user,
                deleted_at__isnull=True,
            )
        )
        raw_type = request.query_params.get('type')
        if raw_type is None and isinstance(request.data, dict):
            raw_type = request.data.get('type')
        if raw_type:
            types = [t.strip() for t in str(raw_type).split(',') if t.strip()]
            valid = {choice[0] for choice in NotificationType.choices}
            types = [t for t in types if t in valid]
            if not types:
                return error_response(
                    "Invalid notification type",
                    code="invalid_notification_type",
                )
            qs = qs.filter(type__in=types)

        count = qs.update(deleted_at=timezone.now())
        return success_response(
            message=f"{count} notifications deleted",
            data={"count": count},
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_notification(request):
    """
    Send a notification manually (admin only)
    """
    user = request.user

    if not (user.is_superuser or user.is_admin()):
        return error_response(
            "Permission denied. Only admins can send notifications.",
            code="permission_denied",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    notification_type = request.data.get('type')
    title = request.data.get('title')
    body = request.data.get('body')
    user_id = request.data.get('user_id')
    company_id = request.data.get('company_id')
    data = request.data.get('data', {})
    image_url = request.data.get('image_url')

    if not notification_type:
        return error_response("type is required", code="missing_field")

    if notification_type not in [choice[0] for choice in NotificationType.choices]:
        return error_response(
            f"Invalid notification type. Valid types: {[choice[0] for choice in NotificationType.choices]}",
            code="invalid_notification_type",
        )

    try:
        if user_id:
            target_user = User.objects.get(id=user_id)
            NotificationService.send_notification(
                user=target_user,
                notification_type=notification_type,
                title=title,
                body=body,
                data=data,
                image_url=image_url,
            )
            return success_response(
                message=f"Notification sent to {target_user.username}",
                data={"user": target_user.username},
            )

        if company_id:
            from companies.models import Company
            company = Company.objects.get(id=company_id)
            roles = request.data.get('roles', None)

            results = NotificationService.send_notification_to_company(
                company=company,
                notification_type=notification_type,
                title=title,
                body=body,
                data=data,
                image_url=image_url,
                roles=roles,
            )
            return success_response(
                message=f"Notifications sent to company {company.name}",
                data={
                    "success": results['success'],
                    "failed": results['failed'],
                },
            )

        return error_response(
            "Either user_id or company_id must be provided",
            code="missing_target",
        )

    except User.DoesNotExist:
        return error_response("User not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return error_response(str(e), code="server_error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _camel_to_snake(name):
    """Convert camelCase to snake_case"""
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _snake_to_camel(name):
    """Convert snake_case to camelCase"""
    components = name.split('_')
    return components[0] + ''.join(x.capitalize() for x in components[1:])


def _convert_notification_types_keys(notification_types, to_snake=True):
    """
    Convert notification_types keys between camelCase (Flutter) and snake_case (Django)
    """
    if not notification_types:
        return {}

    converted = {}
    for key, value in notification_types.items():
        if to_snake:
            new_key = _camel_to_snake(key)
        else:
            new_key = _snake_to_camel(key)
        converted[new_key] = value

    return converted


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def notification_settings(request):
    """
    Get or update notification settings for the authenticated user
    """
    user = request.user

    if request.method == 'GET':
        settings_obj = NotificationSettings.get_or_create_for_user(user)
        serializer = NotificationSettingsSerializer(settings_obj)
        data = serializer.data

        if 'notification_types' in data and data['notification_types']:
            data['notification_types'] = _convert_notification_types_keys(
                data['notification_types'],
                to_snake=False,
            )

        return success_response(data=data)

    elif request.method == 'PUT':
        settings_obj = NotificationSettings.get_or_create_for_user(user)

        request_data = request.data.copy()
        if 'notification_types' in request_data and request_data['notification_types']:
            request_data['notification_types'] = _convert_notification_types_keys(
                request_data['notification_types'],
                to_snake=True,
            )

        serializer = NotificationSettingsSerializer(settings_obj, data=request_data, partial=True)

        if serializer.is_valid():
            serializer.save()
            logger.info(f"Notification settings updated for user {user.username}")

            response_data = serializer.data
            if 'notification_types' in response_data and response_data['notification_types']:
                response_data['notification_types'] = _convert_notification_types_keys(
                    response_data['notification_types'],
                    to_snake=False,
                )

            return success_response(data=response_data)

        logger.error(f"Serializer errors: {serializer.errors}")
        return validation_error_response(serializer.errors)
