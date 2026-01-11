from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone
from .models import Notification, NotificationType
from .serializers import NotificationSerializer, NotificationListSerializer
from .services import NotificationService
from accounts.models import User
import logging

logger = logging.getLogger(__name__)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing and managing user notifications
    """
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    
    def get_queryset(self):
        """Return notifications for the authenticated user"""
        queryset = Notification.objects.filter(user=self.request.user)
        
        # Filter by read status if provided
        read_status = self.request.query_params.get('read', None)
        if read_status is not None:
            read_status = read_status.lower() == 'true'
            queryset = queryset.filter(read=read_status)
        
        # Filter by type if provided
        notification_type = self.request.query_params.get('type', None)
        if notification_type:
            queryset = queryset.filter(type=notification_type)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'list':
            return NotificationListSerializer
        return NotificationSerializer
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark a notification as read"""
        notification = self.get_object()
        notification.mark_as_read()
        return Response({'message': 'Notification marked as read'})
    
    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        """Mark all notifications as read"""
        count = Notification.objects.filter(
            user=request.user,
            read=False
        ).update(read=True, read_at=timezone.now())
        
        return Response({
            'message': f'{count} notifications marked as read'
        })
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """Get count of unread notifications"""
        count = Notification.objects.filter(
            user=request.user,
            read=False
        ).count()
        
        return Response({'unread_count': count})
    
    @action(detail=False, methods=['delete'])
    def delete_all_read(self, request):
        """Delete all read notifications"""
        count, _ = Notification.objects.filter(
            user=request.user,
            read=True
        ).delete()
        
        return Response({
            'message': f'{count} read notifications deleted'
        })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_notification(request):
    """
    Send a notification manually (admin only)
    """
    user = request.user
    
    # Only allow admins and superusers
    if not (user.is_superuser or user.is_admin()):
        return Response(
            {'error': 'Permission denied. Only admins can send notifications.'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    notification_type = request.data.get('type')
    title = request.data.get('title')  # Optional - will use translation if not provided
    body = request.data.get('body')  # Optional - will use translation if not provided
    user_id = request.data.get('user_id')
    company_id = request.data.get('company_id')
    data = request.data.get('data', {})
    image_url = request.data.get('image_url')
    
    # Validate required fields
    if not notification_type:
        return Response(
            {'error': 'type is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Validate notification type
    if notification_type not in [choice[0] for choice in NotificationType.choices]:
        return Response(
            {'error': f'Invalid notification type. Valid types: {[choice[0] for choice in NotificationType.choices]}'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        if user_id:
            # Send to specific user
            target_user = User.objects.get(id=user_id)
            NotificationService.send_notification(
                user=target_user,
                notification_type=notification_type,
                title=title,  # Optional - will use translation if None
                body=body,  # Optional - will use translation if None
                data=data,
                image_url=image_url,
            )
            return Response({
                'message': f'Notification sent to {target_user.username}',
                'user': target_user.username
            })
        
        elif company_id:
            # Send to all users in company
            from companies.models import Company
            company = Company.objects.get(id=company_id)
            roles = request.data.get('roles', None)  # Optional: filter by roles
            
            results = NotificationService.send_notification_to_company(
                company=company,
                notification_type=notification_type,
                title=title,  # Optional - will use translation if None
                body=body,  # Optional - will use translation if None
                data=data,
                image_url=image_url,
                roles=roles,
            )
            return Response({
                'message': f'Notifications sent to company {company.name}',
                'success': results['success'],
                'failed': results['failed']
            })
        
        else:
            return Response(
                {'error': 'Either user_id or company_id must be provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
    except User.DoesNotExist:
        return Response(
            {'error': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
