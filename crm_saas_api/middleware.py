"""
Custom middleware to disable CSRF for API endpoints and validate API keys
"""
import logging
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.conf import settings

logger = logging.getLogger(__name__)


class DisableCSRFForAPI(MiddlewareMixin):
    """
    Middleware to disable CSRF protection for API endpoints
    API endpoints use JWT authentication and don't need CSRF tokens
    """
    
    def process_request(self, request):
        # Disable CSRF for all API endpoints
        if request.path.startswith('/api/'):
            setattr(request, '_dont_enforce_csrf_checks', True)
        return None


class APIKeyValidationMiddleware(MiddlewareMixin):
    """
    Middleware to validate API Key for all API requests
    This prevents unauthorized applications from accessing the API
    """
    
    # List of endpoints that don't require API key (public endpoints)
    PUBLIC_ENDPOINTS = [
        '/api/docs/',
        '/api/schema/',
        '/api/redoc/',
        '/api-auth/',
        '/api/public/',  # Public plans, payment gateways list
        # Payment gateway callbacks: return URLs and webhooks are called by the
        # gateway (browser redirect or server POST) and cannot send X-API-Key.
        '/api/payments/paytabs-return/',
        '/api/payments/zaincash-return/',
        '/api/payments/stripe-return/',
        '/api/payments/qicard-return/',
        '/api/payments/qicard-webhook/',
        '/api/payments/fib-callback/',
        # OAuth callbacks: called by external platforms (Facebook, WhatsApp, etc.)
        # and cannot send X-API-Key header
        '/api/integrations/accounts/oauth/callback/',
        # Webhooks: called by external platforms and cannot send X-API-Key header
        '/api/integrations/webhooks/',
    ]

    def process_request(self, request):
        # Only check API key for API endpoints
        if not request.path.startswith('/api/'):
            return None
        
        # Skip API key validation for public endpoints
        if any(request.path.startswith(endpoint) for endpoint in self.PUBLIC_ENDPOINTS):
            return None
        
        # Get API key from request headers
        # Django converts X-API-Key header to HTTP_X_API_KEY in request.META
        api_key = request.META.get('HTTP_X_API_KEY', '')
        
        # Debug: Log all headers that start with HTTP_X
        if not api_key:
            # Check alternative header names
            api_key = request.META.get('X-API-Key', '')
            if not api_key:
                # Try lowercase
                api_key = request.headers.get('X-API-Key', '') or request.headers.get('x-api-key', '')
        
        # Get allowed API keys from settings
        allowed_keys = [
            getattr(settings, 'API_KEY_MOBILE', ''),
            getattr(settings, 'API_KEY_WEB', ''),
            getattr(settings, 'API_KEY_ADMIN', ''),
        ]
        # Filter out empty keys
        allowed_keys = [key for key in allowed_keys if key]
        
        # Debug logging
        logger.debug(f"API Key Validation - Path: {request.path}")
        logger.debug(f"API Key from request: {'Present' if api_key else 'Missing'}")
        logger.debug(f"Configured API keys: {len(allowed_keys)} keys")
        
        # If no API keys are configured, skip validation (for development)
        if not allowed_keys:
            logger.warning("No API keys configured in settings. Skipping validation.")
            return None
        
        # Validate API key
        if not api_key:
            logger.warning(f"Missing API key for path: {request.path}")
            return JsonResponse(
                {
                    'detail': 'API key is required. Please provide X-API-Key header.',
                    'error': 'Missing API key'
                },
                status=401
            )
        
        if api_key not in allowed_keys:
            logger.warning(f"Invalid API key for path: {request.path}")
            return JsonResponse(
                {
                    'detail': 'Invalid API key. Access denied.',
                    'error': 'Invalid API key'
                },
                status=401
            )
        
        logger.debug("API key validated successfully")
        
        # API key is valid, continue processing
        return None

