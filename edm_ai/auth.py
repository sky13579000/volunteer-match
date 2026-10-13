from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(secret: str, uid: str, ttl: int = 3600) -> str:
    """Stateless HMAC-SHA256 bearer token. For real SSO, terminate OIDC/SAML at a proxy and use header mode."""
    body = _b64(json.dumps({"uid": uid, "exp": int(time.time()) + ttl}).encode())
    return f"{body}.{_b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())}"


def verify(secret: str, token: str) -> str | None:
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        d = json.loads(_unb64(body))
        return d["uid"] if d["exp"] > time.time() else None
    except Exception:
        return None
