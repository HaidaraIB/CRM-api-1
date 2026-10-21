"""
Node-token authentication for the internal telephony API (contract §4–5).

Nodes send ``Authorization: Bearer lpn_…``. Only the SHA-256 is stored, so a
database read does not yield a usable token. ``request.user`` stays anonymous —
a node is not a CRM user — and ``request.auth`` is the ``PbxNode``.
"""

from __future__ import annotations

import hmac

from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions

from . import contract
from .models import PbxNode


class NodeTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        scheme, _, token = header.partition(" ")
        if scheme != self.keyword or not token.startswith("lpn_"):
            return None
        digest = contract.hash_node_token(token.strip())
        node = PbxNode.objects.filter(node_token_hash=digest).first()
        # compare_digest on the stored hash keeps the comparison constant-time
        # even though the lookup itself is by hash.
        if node is None or not hmac.compare_digest(node.node_token_hash, digest):
            raise exceptions.AuthenticationFailed("Invalid node token.")
        return AnonymousUser(), node

    def authenticate_header(self, request):
        return self.keyword
