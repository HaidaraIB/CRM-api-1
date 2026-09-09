"""
Omni-Channel Inbox webhook (Instagram Direct + Facebook Messenger).

One endpoint serves both Meta webhook objects; the handler branches on
payload["object"] ("instagram" | "page").

GET  — hub.mode/hub.verify_token/hub.challenge verification.
POST — signed message events.

Deliberately NOT modelled on whatsapp_webhook.py in one respect: the signature
here is bound to META_INBOX_CLIENT_SECRET with no fallback to META_CLIENT_SECRET.
The inbox is a different Meta app, and a fallback would let anyone holding the
Lead Ads app's secret forge inbox messages into any tenant.
"""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .decorators import rate_limit_webhook
from .services.meta_inbox_ingest import process_entry

logger = logging.getLogger(__name__)

SUPPORTED_OBJECTS = frozenset({'instagram', 'page'})


def verify_meta_inbox_webhook_signature(request) -> bool:
    """HMAC-SHA256 of the raw body with the INBOX app secret only."""
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature or not signature.startswith('sha256='):
        return False

    received_signature = signature[7:]

    app_secret = getattr(settings, 'META_INBOX_CLIENT_SECRET', '')
    if isinstance(app_secret, str):
        app_secret = app_secret.strip()
    if not app_secret:
        logger.warning("META_INBOX_CLIENT_SECRET is not set; rejecting inbox webhook")
        return False

    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(received_signature, expected_signature)


@csrf_exempt
@require_http_methods(["GET", "POST"])
@rate_limit_webhook(max_requests=300, window=60)
def meta_inbox_webhook(request):
    if request.method == 'GET':
        mode = request.GET.get('hub.mode')
        token = (request.GET.get('hub.verify_token') or '').strip()
        challenge = request.GET.get('hub.challenge')

        verify_token = getattr(settings, 'META_INBOX_WEBHOOK_VERIFY_TOKEN', '')
        if isinstance(verify_token, str):
            verify_token = verify_token.strip()

        token_ok = bool(verify_token) and token == verify_token
        if mode == 'subscribe' and token_ok:
            logger.info("Meta Inbox webhook GET verify succeeded")
            return HttpResponse(challenge, content_type='text/plain')

        # These three numbers are what actually diagnose a failed verify in the
        # Meta dashboard; a trailing newline in .env is the usual culprit.
        logger.warning(
            "Meta Inbox webhook GET verify failed: mode=%s token_configured=%s "
            "token_match=%s incoming_token_len=%s expected_token_len=%s",
            mode,
            bool(verify_token),
            token_ok,
            len(token),
            len(verify_token),
        )
        return HttpResponse('Forbidden', status=403)

    allowed_ips = getattr(settings, 'META_INBOX_WEBHOOK_ALLOWED_IPS', None)
    if allowed_ips:
        client_ip = request.META.get('REMOTE_ADDR', '')
        if client_ip not in list(allowed_ips):
            logger.warning("Meta Inbox webhook: IP %s not in allowed list", client_ip)
            return HttpResponse('Forbidden', status=403)

    if not verify_meta_inbox_webhook_signature(request):
        logger.warning("Meta Inbox webhook signature verification failed")
        return HttpResponse('Unauthorized', status=401)

    # Past this point always answer 200. Meta retries on 5xx and eventually
    # disables a webhook that keeps failing, which would silently kill the inbox
    # for every tenant — far worse than dropping one malformed event.
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        logger.warning("Meta Inbox webhook: malformed JSON body")
        return HttpResponse('OK', status=200)

    try:
        webhook_object = str((payload or {}).get('object') or '').strip().lower()
        if webhook_object not in SUPPORTED_OBJECTS:
            logger.info("Meta Inbox webhook: ignoring object=%s", webhook_object)
            return HttpResponse('OK', status=200)

        for entry in (payload.get('entry') or []):
            try:
                process_entry(webhook_object, entry)
            except Exception:
                logger.exception("Meta Inbox webhook: entry processing failed")
    except Exception:
        logger.exception("Meta Inbox webhook: unexpected failure")

    return HttpResponse('OK', status=200)
