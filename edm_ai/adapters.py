from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qs

TOLERANCE = 300           # seconds a signed request stays valid (replay window)
MAX_TEXT = 2800           # chat platforms truncate long messages


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def slack_sign(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack(secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    """Slack request signing (v0). Rejects stale timestamps (replay) and bad signatures in constant time."""
    try:
        if abs((now or time.time()) - int(timestamp)) > TOLERANCE:
            return False
    except (TypeError, ValueError):
        return False
    return _eq(slack_sign(secret, timestamp, body), signature or "")


def webhook_sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_webhook(secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    try:
        if abs((now or time.time()) - int(timestamp)) > TOLERANCE:
            return False
    except (TypeError, ValueError):
        return False
    return _eq(webhook_sign(secret, timestamp, body), signature or "")


def parse_slash(body: bytes) -> dict:
    q = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    return {"user_id": q.get("user_id", ""), "text": q.get("text", "").strip(), "team_id": q.get("team_id", "")}


def format_chat(ans: dict, max_evidence: int = 5) -> str:
    """Plain, Slack-mrkdwn-safe rendering: answer first, then numbered sources the user can open in the web UI."""
    lines = [ans["answer"]]
    if ans["evidence"]:
        lines.append("\n*근거*")
        for e in ans["evidence"][:max_evidence]:
            masked = f" (가려진 컬럼: {', '.join(e['masked_columns'])})" if e["masked_columns"] else ""
            lines.append(f"• [{e['id']}] {e['title']} — `{e['ref']}`{masked}")
        if len(ans["evidence"]) > max_evidence:
            lines.append(f"… 외 {len(ans['evidence']) - max_evidence}건")
    text = "\n".join(lines)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


class ReplayGuard:
    """Rejects an exact signature seen twice inside the tolerance window (webhook replay)."""

    def __init__(self):
        self.seen: dict[str, float] = {}

    def fresh(self, sig: str, now: float | None = None) -> bool:
        now = now or time.time()
        self.seen = {k: t for k, t in self.seen.items() if now - t < TOLERANCE}
        if sig in self.seen:
            return False
        self.seen[sig] = now
        return True
