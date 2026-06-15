"""Helpers for optional one-click email unsubscribe links."""

from __future__ import annotations

import hmac
from hashlib import sha256
from urllib.parse import urlencode


def unsubscribe_token(email: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), email.lower().encode("utf-8"), sha256).hexdigest()


def valid_unsubscribe_token(email: str, token: str, secret: str) -> bool:
    expected = unsubscribe_token(email, secret)
    return hmac.compare_digest(expected, token)


def unsubscribe_url(base_url: str, email: str, secret: str) -> str:
    base = base_url.rstrip("/")
    query = urlencode({"email": email.lower(), "token": unsubscribe_token(email, secret)})
    return f"{base}/unsubscribe?{query}"
