"""
Background tasks for integrations
"""
from django.utils import timezone
from datetime import timedelta
from .models import IntegrationAccount, IntegrationLog
from .oauth_utils import get_oauth_handler
import logging

logger = logging.getLogger(__name__)


def refresh_expired_tokens():
    """
    تجديد Access Tokens المنتهية الصلاحية تلقائياً
    
    يجب استدعاء هذه المهمة بشكل دوري (مثلاً كل ساعة)
    يمكن استخدام Django Q2 أو Celery
    """
    # البحث عن Tokens التي ستنتهي خلال 24 ساعة
    expiry_threshold = timezone.now() + timedelta(hours=24)
    
    accounts_to_refresh = IntegrationAccount.objects.filter(
        status='connected',
        token_expires_at__lte=expiry_threshold,
        token_expires_at__isnull=False
    )
    
    refreshed_count = 0
    failed_count = 0
    
    for account in accounts_to_refresh:
        try:
            refresh_token = account.get_refresh_token()
            
            if not refresh_token:
                logger.warning(f"No refresh token for account {account.id}")
                continue
            
            # تجديد Token
            oauth_handler = get_oauth_handler(account.platform)
            token_data = oauth_handler.refresh_token(refresh_token)
            
            # تحديث Account
            account.set_access_token(token_data['access_token'])
            if 'refresh_token' in token_data:
                account.set_refresh_token(token_data['refresh_token'])
            
            expires_in = token_data.get('expires_in', 0)
            if expires_in:
                account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
            
            account.status = 'connected'
            account.error_message = None
            account.save()
            
            refreshed_count += 1
            
            # تسجيل العملية
            IntegrationLog.objects.create(
                account=account,
                action='auto_refresh_token',
                status='success',
                message='Token refreshed automatically',
            )
            
            logger.info(f"Successfully refreshed token for account {account.id}")
            
        except Exception as e:
            failed_count += 1
            account.status = 'expired'
            account.error_message = str(e)
            account.save()
            
            IntegrationLog.objects.create(
                account=account,
                action='auto_refresh_token',
                status='error',
                message='Failed to refresh token automatically',
                error_details=str(e),
            )
            
            logger.error(f"Failed to refresh token for account {account.id}: {str(e)}")
    
    logger.info(f"Token refresh completed: {refreshed_count} succeeded, {failed_count} failed")
    
    return {
        'refreshed': refreshed_count,
        'failed': failed_count,
        'total': accounts_to_refresh.count()
    }



