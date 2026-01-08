"""
Decorators for integration endpoints
"""
from functools import wraps
from django.http import JsonResponse
from django.core.cache import cache
import hashlib
import logging

logger = logging.getLogger(__name__)


def rate_limit_webhook(max_requests=100, window=60):
    """
    Rate limiting decorator للـ webhooks
    
    Args:
        max_requests: عدد الطلبات المسموح بها
        window: النافذة الزمنية بالثواني
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            # استخدام IP address كمعرف
            ip_address = request.META.get('REMOTE_ADDR', 'unknown')
            
            # إنشاء مفتاح cache
            cache_key = f"webhook_rate_limit:{ip_address}"
            
            # الحصول على عدد الطلبات الحالي
            current_requests = cache.get(cache_key, 0)
            
            if current_requests >= max_requests:
                logger.warning(f"Rate limit exceeded for IP: {ip_address}")
                return JsonResponse(
                    {'error': 'Rate limit exceeded. Please try again later.'},
                    status=429
                )
            
            # زيادة العداد
            cache.set(cache_key, current_requests + 1, window)
            
            return view_func(request, *args, **kwargs)
        
        return wrapped_view
    return decorator



