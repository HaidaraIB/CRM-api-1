"""
Notification service for sending push notifications via Firebase Cloud Messaging
"""
import logging
import os
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Notification, NotificationType, NotificationSettings
from .translations import get_notification_text, normalize_notification_language
from .fcm_android_channels import (
    android_notification_channel_id,
    android_notification_raw_sound_basename,
    ios_notification_sound_filename,
    tenant_chat_apns_collapse_id,
    tenant_chat_ios_sound_filename,
)

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
                firebase_credentials_path = getattr(
                    settings, "FIREBASE_CREDENTIALS_PATH", ""
                )

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
        skip_database_insert: bool = False,
    ) -> bool:
        """
        Persist an in-app notification and best-effort FCM push.

        Hard rule: the Notification inbox row is written whenever this method is
        asked to insert (skip_database_insert=False), independent of push.
        Quiet hours, missing FCM tokens, Firebase init failure, and FCM send
        errors must never block persistence.

        Per-type / master mute (is_notification_enabled) can skip both insert
        and push when the user opted out of that type. Quiet hours and delivery
        failures only affect push.

        Args:
            user: User to notify
            notification_type: Type of notification (from NotificationType)
            title: Notification title (optional, will use translation if not provided)
            body: Notification body (optional, will use translation if not provided)
            data: Additional data payload (used for formatting translated messages)
            image_url: Optional image URL
            language: Language code ('ar' or 'en'). If not provided, uses user.language or 'ar'
            lead_source: Optional lead source (for source filtering on push)
            sender_role: Optional sender role (for role filtering on push)
            skip_settings_check: Skip mute/quiet-hour checks for push (admin/system);
                does not skip inbox insert when skip_database_insert is False
            skip_database_insert: If True, do not write Notification rows (caller already persisted)

        Returns:
            True if at least one FCM push succeeded; False otherwise (inbox may
            still have been written).
        """
        settings_obj = None
        if not skip_settings_check:
            try:
                settings_obj = NotificationSettings.get_or_create_for_user(user)
                # Explicit mute: skip both inbox and push.
                if not settings_obj.is_notification_enabled(notification_type):
                    logger.info(
                        f"Notification {notification_type} muted for user {user.username}; "
                        f"skipping inbox and push"
                    )
                    return False
            except Exception as e:
                logger.warning(
                    f"Error checking notification settings for user {user.username}: {e}"
                )
                settings_obj = None  # fail open for mute + push

        # Recipient language from DB (avoids stale request-scoped user instances)
        try:
            fresh_user = User.objects.only("language").get(pk=user.pk)
            user_language = normalize_notification_language(
                language or fresh_user.language or getattr(user, "language", None)
            )
        except Exception:
            user_language = normalize_notification_language(
                language or getattr(user, "language", None)
            )

        # Get translated text if title/body not provided
        if title is None or body is None:
            translated = get_notification_text(
                notification_type=notification_type,
                language=user_language,
                **(data or {})
            )
            title = title or translated['title']
            body = body or translated['body']

        # Inbox first — never gated on push eligibility or delivery.
        if not skip_database_insert:
            try:
                Notification.objects.create(
                    user=user,
                    type=notification_type,
                    title=title,
                    body=body,
                    data=data or {},
                    image_url=image_url,
                )
            except Exception as e:
                logger.error(
                    f"Error saving notification for user {user.username}: {e}"
                )
                return False

        # Push is best-effort after persist.
        allow_push = True
        if not skip_settings_check and settings_obj is not None:
            try:
                allow_push = settings_obj.should_send_notification(
                    notification_type=notification_type,
                    lead_source=lead_source,
                    sender_role=sender_role,
                )
                if not allow_push:
                    logger.info(
                        f"Push for {notification_type} skipped for user {user.username} "
                        f"(quiet hours / source / role prefs); inbox row kept"
                    )
            except Exception as e:
                logger.warning(
                    f"Error checking push settings for user {user.username}: {e}"
                )
                allow_push = True  # fail open for push

        if not allow_push:
            return False

        if not cls.initialize():
            logger.warning(
                "Firebase not initialized. Inbox saved; push not sent for %s.",
                user.username,
            )
            return False

        user_tokens = user.iter_fcm_tokens_for_push()
        if not user_tokens:
            logger.warning(
                f"User {user.username} has no FCM token. Inbox saved; push not sent."
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

            # Team chat: Android data-only so Flutter merges lines in one tray item.
            # iOS uses APNs alert + custom sound (Point pattern) — background data-only
            # pushes are unreliable on iOS and never play custom sounds from local handlers.
            tenant_chat_data_only = (data or {}).get("kind") == "tenant_chat"

            success_count = 0
            for token in user_tokens:
                try:
                    if tenant_chat_data_only:
                        conversation_id = (data or {}).get("conversation_id")
                        collapse_id = tenant_chat_apns_collapse_id(conversation_id)
                        apns_headers: Dict[str, str] = {
                            "apns-push-type": "alert",
                            "apns-priority": "10",
                        }
                        if collapse_id:
                            apns_headers["apns-collapse-id"] = collapse_id
                        apns_aps_kwargs: Dict[str, Any] = {
                            "alert": messaging.ApsAlert(title=title, body=body),
                            "sound": tenant_chat_ios_sound_filename(),
                        }
                        thread_id = (
                            str(conversation_id).strip()
                            if conversation_id is not None
                            and str(conversation_id).strip()
                            else None
                        )
                        if thread_id:
                            apns_aps_kwargs["thread_id"] = thread_id
                        message = messaging.Message(
                            data=message_data,
                            token=token,
                            android=messaging.AndroidConfig(priority="high"),
                            apns=messaging.APNSConfig(
                                headers=apns_headers,
                                payload=messaging.APNSPayload(
                                    aps=messaging.Aps(**apns_aps_kwargs),
                                ),
                            ),
                        )
                        logger.info(
                            "FCM tenant_chat android=data-only ios_sound=%s collapse=%s",
                            tenant_chat_ios_sound_filename(),
                            collapse_id or "(none)",
                        )
                    else:
                        # Android 8+: system-displayed FCM uses the *channel* sound, not the
                        # legacy per-notification sound, when posting to the default FCM channel.
                        # So we must send channel_id matching flutter_local_notifications channels
                        # (created on first app open). If those channels do not exist yet, Android
                        # may drop the notification — user must open the app once after install.
                        # team_activity reuses category channels/sounds based on data.action.
                        action = (data or {}).get("action")
                        action_str = str(action) if action is not None else None
                        channel_id = android_notification_channel_id(
                            notification_type, action=action_str
                        )
                        sound_base = android_notification_raw_sound_basename(
                            notification_type, action=action_str
                        )
                        ios_sound = ios_notification_sound_filename(
                            notification_type, action=action_str
                        )
                        android_notif_kwargs: Dict[str, Any] = {
                            "channel_id": channel_id,
                        }
                        if sound_base:
                            android_notif_kwargs["sound"] = sound_base
                        apns_aps_kwargs: Dict[str, Any] = {}
                        if ios_sound:
                            apns_aps_kwargs["sound"] = ios_sound
                        message = messaging.Message(
                            notification=notification_payload,
                            data=message_data,
                            token=token,
                            android=messaging.AndroidConfig(
                                priority="high",
                                notification=messaging.AndroidNotification(
                                    **android_notif_kwargs,
                                ),
                            ),
                            apns=messaging.APNSConfig(
                                headers={"apns-push-type": "alert", "apns-priority": "10"},
                                payload=messaging.APNSPayload(
                                    aps=messaging.Aps(**apns_aps_kwargs),
                                ),
                            ),
                        )
                        logger.info(
                            "FCM android channel_id=%s android_sound=%s ios_sound=%s type=%s action=%s",
                            channel_id,
                            sound_base or "(default)",
                            ios_sound or "(default)",
                            notification_type,
                            action_str or "(none)",
                        )
                    response = messaging.send(message)
                    logger.info(
                        f"Notification sent to {user.username} token={token[:12]}...: {response}"
                    )
                    success_count += 1
                except messaging.UnregisteredError:
                    logger.warning(
                        f"FCM token for user {user.username} is invalid. Removing token."
                    )
                    user.remove_fcm_token(token)
                    user.save(update_fields=["fcm_token", "fcm_tokens"])

            return success_count > 0

        except Exception as e:
            logger.error(f"Error sending notification to {user.username}: {e}")
            # Inbox already saved; push failure must not undo that.
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
        
        # Filter users with at least one FCM token (legacy or multi-device).
        users = users.filter(
            (Q(fcm_token__isnull=False) & ~Q(fcm_token="")) | ~Q(fcm_tokens=[])
        )
        
        return cls.send_notification_to_multiple(
            list(users),
            notification_type,
            title,
            body,
            data,
            image_url,
        )
