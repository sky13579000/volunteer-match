from __future__ import annotations

import base64
import re
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import adapters, auth
from .config import App
from .edm import EdmError

log = logging.getLogger("edm_ai")
STATIC = Path(__file__).parent / "static"
FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css")}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"


def make_handler(app: App, demo_login: bool):
    acfg = app.cfg.get("auth", {"mode": "header"})
    secret = os.environ.get(acfg.get("secret_env", "EDM_AI_SECRET"), "")
    if acfg["mode"] == "token" and len(secret) < 16:
        raise SystemExit("token auth needs env EDM_AI_SECRET (>=16 chars)")
    limit = int(app.cfg.get("rate_limit_per_min", 60))
    hits: dict[str, deque] = defaultdict(deque)
    acfg_ad = app.cfg.get("adapters", {})
    slack_secret = os.environ.get((acfg_ad.get("slack") or {}).get("signing_secret_env", ""), "") if acfg_ad.get("slack") else ""
    hook_secret = os.environ.get((acfg_ad.get("webhook") or {}).get("secret_env", ""), "") if acfg_ad.get("webhook") else ""
    for name, cfg_, sec in (("slack", acfg_ad.get("slack"), slack_secret), ("webhook", acfg_ad.get("webhook"), hook_secret)):
        if cfg_ and sec and len(sec) < 16:      # unset secret = adapter disabled; a WEAK secret is refused
            raise SystemExit(f"adapter '{name}': secret must be at least 16 characters")
        if cfg_ and not sec:
            log.info("adapter '%s' disabled (secret env var not set)", name)
    replay = adapters.ReplayGuard()
    lock = app.lock  # DB connections are shared across request threads and the re-indexer

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj, ctype="application/json"):
            body = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _user(self):
            if acfg["mode"] == "token":
                tok = self.headers.get("Authorization", "")
                return app.users.get(auth.verify(secret, tok[7:]) or "") if tok.startswith("Bearer ") else None
            # header mode: ONLY behind an authenticating proxy (SSO/mTLS) that strips client-supplied copies.
            return app.users.get(self.headers.get(acfg.get("header", "X-User-Id"), ""))

        def _limited(self, uid):
            now, q = time.time(), hits[uid]
            while q and q[0] < now - 60:
                q.popleft()
            q.append(now)
            return len(q) > limit

        def do_GET(self):
            self._safe(self._get)

        def do_POST(self):
            self._safe(self._post)

        def do_PUT(self):
            self._safe(self._put)

        def do_DELETE(self):
            self._safe(self._delete)

        def _body(self, limit: int) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            if n > limit:
                raise EdmError(413, "요청이 너무 큽니다")
            try:
                d = json.loads(self.rfile.read(n))
                assert isinstance(d, dict)
                return d
            except Exception:
                raise EdmError(400, "bad request")

        def _doc_route(self, path: str):
            m = re.fullmatch(r"/api/docs/(\d+)(/acl)?", path)
            return (int(m.group(1)), bool(m.group(2))) if m else None

        def _edm(self, fn):
            try:
                with lock:
                    return self._send(200, fn())
            except EdmError as e:
                return self._send(e.code, {"error": e.msg})

        def _safe(self, fn):
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # never drop the connection or leak internals
                log.exception("request failed: %s %s", self.command, self.path)
                try:
                    self._send(500, {"error": "internal error"})
                except Exception:
                    pass

        def _get(self):
            url = urlparse(self.path)
            path = url.path
            if path in FILES:
                name, ctype = FILES[path]
                return self._send(200, (STATIC / name).read_text("utf-8"), ctype)
            if path == "/api/config":
                return self._send(200, {"auth_mode": acfg["mode"], "demo": demo_login and acfg["mode"] == "header",
                                        "examples": app.cfg.get("examples", [])})
            if path == "/api/demo-users":
                if not (demo_login and acfg["mode"] == "header"):
                    return self._send(404, {"error": "not found"})
                return self._send(200, [{"id": x.id, "name": x.name, "dept": x.dept, "level": x.level} for x in app.users.values()])
            u = self._user()
            if path == "/api/health":
                return self._send(200, {"ok": True})
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            if path == "/api/me":
                return self._send(200, {"id": u.id, "name": u.name, "dept": u.dept, "level": u.level, "is_admin": "admin" in u.roles})
            if path == "/api/docs":
                q = (parse_qs(url.query).get("q") or [""])[0].strip()[:200] or None
                return self._edm(lambda: app.edm.list(u, q))
            if path == "/api/alerts":
                return self._edm(lambda: app.edm.alerts(u))
            route = self._doc_route(path)
            if route and not route[1]:
                return self._edm(lambda: app.edm.get(u, route[0]))
            if path == "/api/audit":
                if "admin" not in u.roles:
                    return self._send(403, {"error": "forbidden"})
                with lock:
                    ok, bad = app.store.verify_audit()
                    return self._send(200, {"chain_ok": ok, "first_bad_id": bad, "entries": app.store.audit_log()})
            self._send(404, {"error": "not found"})

        def _adapter(self, path: str):
            """Slack slash command / signed webhook. Authenticated by signature, never by the user headers."""
            n = int(self.headers.get("Content-Length", 0))
            if n > 65536:
                return self._send(413, {"error": "too large"})
            raw = self.rfile.read(n)
            if path == "/api/adapters/slack" and slack_secret:
                if not adapters.verify_slack(slack_secret, self.headers.get("X-Slack-Request-Timestamp", ""), raw,
                                             self.headers.get("X-Slack-Signature", "")):
                    return self._send(401, {"error": "bad signature"})
                cmd = adapters.parse_slash(raw)
                uid = app.external.get(("slack", cmd["user_id"]))
                if not uid:
                    return self._send(200, {"response_type": "ephemeral", "text": "이 Slack 계정은 EDM-AI 사용자와 연결되어 있지 않습니다. 관리자에게 문의하세요."})
                if not cmd["text"]:
                    return self._send(200, {"response_type": "ephemeral", "text": "사용법: /edm 한빛전자 계약 금액은?"})
                if self._limited(uid):
                    return self._send(200, {"response_type": "ephemeral", "text": "요청이 너무 많습니다. 잠시 후 다시 시도하세요."})
                with lock:
                    ans = app.engine.ask(app.users[uid], cmd["text"][:2000]).to_dict()
                return self._send(200, {"response_type": "ephemeral", "text": adapters.format_chat(ans)})   # ephemeral: only the asker sees it
            if path == "/api/adapters/webhook" and hook_secret:
                ts, sig = self.headers.get("X-EDM-Timestamp", ""), self.headers.get("X-EDM-Signature", "")
                if not adapters.verify_webhook(hook_secret, ts, raw, sig):
                    return self._send(401, {"error": "bad signature"})
                if not replay.fresh(sig):
                    return self._send(409, {"error": "replayed request"})
                try:
                    b = json.loads(raw)
                    ident, q = b["user"], str(b["question"]).strip()[:2000]
                    uid = app.external.get((str(ident["type"]), str(ident["id"])))
                    assert q
                except Exception:
                    return self._send(400, {"error": "bad request"})
                if not uid:
                    return self._send(403, {"error": "unknown external user"})
                if self._limited(uid):
                    return self._send(429, {"error": "rate limited"})
                with lock:
                    ans = app.engine.ask(app.users[uid], q).to_dict()
                ans["text"] = adapters.format_chat(ans)
                return self._send(200, ans)
            return self._send(404, {"error": "not found"})

        def _post(self):
            if urlparse(self.path).path.startswith("/api/adapters/"):
                return self._adapter(urlparse(self.path).path)
            u = self._user()
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            path = urlparse(self.path).path
            if self._limited(u.id):
                return self._send(429, {"error": "rate limited"})
            if path == "/api/docs":
                def up():
                    b = self._body(16 * 1024 * 1024)
                    try:
                        data = base64.b64decode(b.get("content_b64", ""), validate=True)
                    except Exception:
                        raise EdmError(400, "content_b64 가 올바르지 않습니다")
                    return app.edm.upload(u, b.get("filename", ""), data, b.get("acl"))
                return self._edm(up)
            if path != "/api/ask":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                q = str(json.loads(self.rfile.read(min(n, 65536)))["question"]).strip()
                assert q
            except Exception:
                return self._send(400, {"error": "bad request"})
            with lock:
                result = app.engine.ask(u, q).to_dict()
            self._send(200, result)

        def _put(self):
            u = self._user()
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            route = self._doc_route(urlparse(self.path).path)
            if not (route and route[1]):
                return self._send(404, {"error": "not found"})
            if self._limited(u.id):
                return self._send(429, {"error": "rate limited"})
            return self._edm(lambda: app.edm.set_acl(u, route[0], self._body(65536).get("acl")))

        def _delete(self):
            u = self._user()
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            route = self._doc_route(urlparse(self.path).path)
            if not (route and not route[1]):
                return self._send(404, {"error": "not found"})
            if self._limited(u.id):
                return self._send(429, {"error": "rate limited"})
            return self._edm(lambda: app.edm.delete(u, route[0]))

        def log_message(self, *a):
            pass

    return H


def _reindex_loop(app: App, every: int):
    while True:
        time.sleep(every)
        try:
            st = app.ingest()
            if st["added"] or st["pruned"] or st["skipped"]:
                log.info("reindex: %s", st)
        except Exception:
            log.exception("reindex failed (serving previous index)")


def serve(app: App, host="127.0.0.1", port=8080, demo_login=False, ingest=False, reindex_every=0):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if ingest:
        log.info("startup ingest: %s", app.ingest())
    if reindex_every:
        threading.Thread(target=_reindex_loop, args=(app, reindex_every), daemon=True).start()
    ThreadingHTTPServer((host, port), make_handler(app, demo_login)).serve_forever()
