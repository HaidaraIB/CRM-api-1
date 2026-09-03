"""
ASGI config for crm_saas_api.

**This is not what serves the API.** Gunicorn serves ``wsgi.py`` and that has not
changed. This module exists for one thing: a separate uvicorn process
(``deploy/crm-realtime.service``) that handles only ``/ws/`` and only WebSockets.

Keeping them apart is the whole point. Gunicorn runs ``gthread`` with a small,
fixed number of request slots, and a WebSocket held open inside that pool would
occupy one for the life of the connection — a few hundred open tabs and the API
stops answering anyone. On an asyncio process the same connections are cheap,
because an idle socket costs a file descriptor rather than a worker thread.

The ``http`` route below is therefore never exercised in production: nginx proxies
HTTP to gunicorn on :8000 and only ``/ws/`` to uvicorn on :8001. It is present so
this file remains a valid, complete ASGI application — for local development and
for anyone who runs it directly.

For more information, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm_saas_api.settings')

# Must be built before importing anything that touches models or settings-derived
# state — routing imports the consumer, which imports auth, which needs the app
# registry populated.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from realtime.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # No AuthMiddlewareStack: browsers cannot set headers on a WebSocket
        # handshake, so there is no session cookie or Authorization header to
        # read. The consumer authenticates the JWT from the query string itself
        # (realtime/auth.py).
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
