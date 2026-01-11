"""
Notification service for sending push notifications via Firebase Cloud Messaging
"""
import logging
import os
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from django.conf import settings
from django.contrib.auth import get_user_model
from .models import Notification, NotificationType, NotificationSettings
from .translations import get_notification_text

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger(__name__)
User = get_user_model()

# Try to import Firebase Admin SDK
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.warning("Firebase Admin SDK not installed. Install it with: pip install firebase-admin")


class NotificationService:
    """Service for sending push notifications"""
    
    _initialized = False
    
    @classmethod
    def initialize(cls):
        """Initialize Firebase Admin SDK"""
        if not FIREBASE_AVAILABLE:
            logger.warning("Firebase Admin SDK not available. Notifications will not be sent.")
            return False
        
        if cls._initialized:
            return True
        
        try:
            # Check if Firebase is already initialized
            if not firebase_admin._apps:
                # Get Firebase credentials from environment or settings
                firebase_credentials_path = os.getenv('FIREBASE_CREDENTIALS_PATH')
                
                if firebase_credentials_path and os.path.exists(firebase_credentials_path):
                    cred = credentials.Certificate(firebase_credentials_path)
                    firebase_admin.initialize_app(cred)
                    logger.info("Firebase Admin SDK initialized from credentials file")
                else:
                    # Try to use default credentials (for production with GOOGLE_APPLICATION_CREDENTIALS)
                    try:
                        firebase_admin.initialize_app()
                        logger.info("Firebase Admin SDK initialized with default credentials")
                    except Exception as e:
                        logger.warning(f"Could not initialize Firebase: {e}")
                        logger.warning("Set FIREBASE_CREDENTIALS_PATH or GOOGLE_APPLICATION_CREDENTIALS environment variable")
                        return False
            else:
                logger.info("Firebase Admin SDK already initialized")
            
            cls._initialized = True
            return True
        except Exception as e:
            logger.error(f"Error initializing Firebase Admin SDK: {e}")
            return False
    
    @classmethod
    def send_notification(
        cls,
        user: "AbstractUser",
        notification_type: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
        language: Optional[str] = None,
        lead_source: Optional[str] = None,
        sender_role: Optional[str] = None,
        skip_settings_check: bool = False,
    ) -> bool:
        """
        Send a push notification to a user
        
        Args:
            user: User to send notification to
            notification_type: Type of notification (from NotificationType)
            title: Notification title (optional, will use translation if not provided)
            body: Notification body (optional, will use translation if not provided)
            data: Additional data payload (used for formatting translated messages)
            image_url: Optional image URL
            language: Language code ('ar' or 'en'). If not provided, uses user.language or 'ar'
            lead_source: Optional lead source (for source filtering)
            sender_role: Optional sender role (for role filtering)
            skip_settings_check: Skip notification settings check (for admin/system notifications)
        """
        # Check user notification settings (unless explicitly skipped)
        if not skip_settings_check:
            try:
                settings_obj = NotificationSettings.get_or_create_for_user(user)
                
                if not settings_obj.should_send_notification(
                    notification_type=notification_type,
                    lead_source=lead_source,
                    sender_role=sender_role
                ):
                    logger.info(
                        f"Notification {notification_type} skipped for user {user.username} "
                        f"due to notification settings"
                    )
                    return False
            except Exception as e:
                logger.warning(f"Error checking notification settings for user {user.username}: {e}")
                # Continue with sending if settings check fails (fail open)
        
        # Get user language
        user_language = language or getattr(user, 'language', 'ar') or 'ar'
        
        # Get translated text if title/body not provided
        if title is None or body is None:
            translated = get_notification_text(
                notification_type=notification_type,
                language=user_language,
                **(data or {})
            )
            title = title or translated['title']
            body = body or translated['body']
        
        if not cls.initialize():
            logger.warning("Firebase not initialized. Saving notification to database only.")
            # Still save to database even if Firebase is not available
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                data=data or {},
                image_url=image_url,
            )
            return False
        
        if not user.fcm_token:
            logger.warning(f"User {user.username} has no FCM token. Notification not sent.")
            # Still save to database
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                data=data or {},
                image_url=image_url,
            )
            return False
        
        try:
            # Prepare notification payload
            notification_payload = messaging.Notification(
                title=title,
                body=body,
                image=image_url,
            )
            
            # Prepare data payload
            message_data = {
                'type': notification_type,
                'title': title,
                'body': body,
            }
            
            if data:
                # Add data fields (convert to strings for FCM)
                for key, value in data.items():
                    message_data[key] = str(value)
            
            if image_url:
                message_data['image_url'] = image_url
            
            # Create message
            message = messaging.Message(
                notification=notification_payload,
                data=message_data,
                token=user.fcm_token,
            )
            
            # Send message
            response = messaging.send(message)
            logger.info(f"Notification sent to {user.username}: {response}")
            
            # Save to database
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                data=data or {},
                image_url=image_url,
            )
            
            return True
            
        except messaging.UnregisteredError:
            # Token is invalid, remove it
            logger.warning(f"FCM token for user {user.username} is invalid. Removing token.")
            user.fcm_token = None
            user.save(update_fields=['fcm_token'])
            return False
            
        except Exception as e:
            logger.error(f"Error sending notification to {user.username}: {e}")
            # Still save to database
            Notification.objects.create(
                user=user,
                type=notification_type,
                title=title,
                body=body,
                data=data or {},
                image_url=image_url,
            )
            return False
    
    @classmethod
    def send_notification_to_multiple(
        cls,
        users: List["AbstractUser"],
        notification_type: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        Send notification to multiple users
        
        Returns:
            Dict with 'success' and 'failed' counts
        """
        results = {'success': 0, 'failed': 0}
        
        for user in users:
            if cls.send_notification(user, notification_type, title, body, data, image_url):
                results['success'] += 1
            else:
                results['failed'] += 1
        
        return results
    
    @classmethod
    def send_notification_to_company(
        cls,
        company,
        notification_type: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
        roles: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """
        Send notification to all users in a company
        
        Args:
            company: Company instance
            notification_type: Type of notification
            title: Notification title (optional, will use translation if not provided)
            body: Notification body (optional, will use translation if not provided)
            data: Additional data (used for formatting translated messages)
            image_url: Optional image URL
            roles: Optional list of roles to filter (e.g., ['admin', 'employee'])
        """
        users = User.objects.filter(company=company, is_active=True)
        
        if roles:
            users = users.filter(role__in=roles)
        
        # Filter users with FCM tokens
        users = users.exclude(fcm_token__isnull=True).exclude(fcm_token='')
        
        return cls.send_notification_to_multiple(
            list(users),
            notification_type,
            title,
            body,
            data,
            image_url,
        )
