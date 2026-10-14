"""Outbound event webhooks: tell other systems (groupware, ERP, ESB, SIEM) when something happens to a document.

config.json:
  "events": {"include_title": false, "webhooks": [
      {"name": "groupware", "url": "https://gw.example/edm-hook", "secret_env": "EDM_AI_HOOK_GW", "events": ["doc.approved", "doc.rejected"]}]}

Each delivery is a JSON POST signed like the inbound adapters: X-EDM-Timestamp, X-EDM-Signature = sha256 HMAC(secret, ts + "." + body),
X-EDM-Event, X-EDM-Delivery (unique id, use it to de-duplicate). Sending happens on a background thread with retries, so a slow or
dead receiver never delays or breaks a user's request. Only http/https URLs from the (administrator-written) config are used."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger("edm_ai")

EVENT_OF_ACTION = {"registered": "doc.created", "new_version": "doc.version_added", "restored": "doc.version_added", "rejected": "doc.rejected", "deleted": "doc.deleted",
                   "expired": "doc.expired", "acl_changed": "doc.acl_changed", "moved": "doc.moved", "legal_hold_on": "doc.legal_hold", "legal_hold_off": "doc.legal_hold_released",
                   "dlp_detected": "privacy.detected", "submitted": "approval.requested", "access_requested": "access.requested", "access_granted": "access.granted"}
ALL_EVENTS = sorted(set(EVENT_OF_ACTION.values()) | {"doc.approved", "disposal.completed", "ping"})
RETRY_WAIT = (0, 2, 10)


class Events:
    def __init__(self, cfg: dict, timeout: float = 8.0, wait=RETRY_WAIT):
        self.include_title = bool(cfg.get("include_title", False))
        self.hooks = []
        for h in cfg.get("webhooks", []):
            url = str(h.get("url", ""))
            if not url.startswith(("http://", "https://")):
                raise SystemExit(f"events.webhooks[{h.get('name')}]: url must be http(s)")
            secret = os.environ.get(h.get("secret_env", ""), "")
            if len(secret) < 16:
                log.warning("webhook '%s' disabled: secret env var missing or shorter than 16 characters", h.get("name"))
                continue
            bad = [e for e in h.get("events", ["*"]) if e != "*" and e not in ALL_EVENTS]
            if bad:
                raise SystemExit(f"events.webhooks[{h.get('name')}]: unknown events {bad}")
            self.hooks.append({"name": str(h.get("name") or url)[:40], "url": url, "secret": secret, "events": set(h.get("events", ["*"]))})
        self.timeout, self.wait = timeout, wait
        self.q: queue.Queue = queue.Queue(maxsize=2000)
        self.deliveries: deque = deque(maxlen=200)
        self.sent = self.failed = 0
        if self.hooks:
            threading.Thread(target=self._run, daemon=True, name="edm-events").start()

    def emit(self, event: str, row: dict | None, actor: str, detail: str = ""):
        """Never raises and never blocks: a full queue drops the event (and says so in the log)."""
        targets = [h for h in self.hooks if "*" in h["events"] or event in h["events"]]
        if not targets:
            return
        doc = None
        if row:
            doc = {"id": row.get("id"), "doc_no": row.get("doc_no"), "doc_type": row.get("doc_type"), "status": row.get("status"), "owner": row.get("owner"), "level": row.get("level")}
            if self.include_title:
                doc["title"] = row.get("title")
        payload = {"id": str(uuid.uuid4()), "type": event, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": actor, "doc": doc, "detail": detail[:200]}
        for h in targets:
            try:
                self.q.put_nowait((h, payload))
            except queue.Full:
                log.warning("event queue full: dropped %s for %s", event, h["name"])

    @staticmethod
    def sign(secret: str, ts: str, body: bytes) -> str:
        return "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()

    def _post(self, h: dict, payload: dict) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode()
        ts = str(int(time.time()))
        req = urllib.request.Request(h["url"], body, {"Content-Type": "application/json; charset=utf-8", "X-EDM-Timestamp": ts, "X-EDM-Signature": self.sign(h["secret"], ts, body),
                                                       "X-EDM-Event": payload["type"], "X-EDM-Delivery": payload["id"], "User-Agent": "icc-jeju-edm/1"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status

    def _deliver(self, h: dict, payload: dict):
        last = ""
        for n, wait in enumerate(self.wait, 1):
            if wait:
                time.sleep(wait)
            try:
                code = self._post(h, payload)
                if 200 <= code < 300:
                    self.sent += 1
                    self.deliveries.appendleft({"ts": payload["ts"], "hook": h["name"], "event": payload["type"], "delivery": payload["id"], "status": "ok", "attempts": n, "http": code, "error": ""})
                    return
                last = f"HTTP {code}"
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if 400 <= e.code < 500 and e.code not in (408, 429):
                    break                                                       # the receiver rejected it: retrying will not help
            except Exception as e:                                              # network errors, timeouts
                last = type(e).__name__
        self.failed += 1
        self.deliveries.appendleft({"ts": payload["ts"], "hook": h["name"], "event": payload["type"], "delivery": payload["id"], "status": "failed", "attempts": n, "http": None, "error": last})
        log.warning("webhook %s failed for %s: %s", h["name"], payload["type"], last)

    def _run(self):
        while True:
            h, payload = self.q.get()
            try:
                self._deliver(h, payload)
            except Exception:
                log.exception("webhook delivery crashed")

    def status(self) -> dict:
        return {"hooks": [{"name": h["name"], "url": h["url"].split("?")[0], "events": sorted(h["events"])} for h in self.hooks], "queued": self.q.qsize(), "sent": self.sent, "failed": self.failed,
                "recent": list(self.deliveries)[:50], "available_events": ALL_EVENTS}
