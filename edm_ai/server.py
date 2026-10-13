from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth
from .config import App

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
            if self.path in FILES:
                name, ctype = FILES[self.path]
                return self._send(200, (STATIC / name).read_text("utf-8"), ctype)
            if self.path == "/api/config":
                return self._send(200, {"auth_mode": acfg["mode"], "demo": demo_login and acfg["mode"] == "header",
                                        "examples": app.cfg.get("examples", [])})
            if self.path == "/api/demo-users":
                if not (demo_login and acfg["mode"] == "header"):
                    return self._send(404, {"error": "not found"})
                return self._send(200, [{"id": x.id, "name": x.name, "dept": x.dept, "level": x.level} for x in app.users.values()])
            u = self._user()
            if self.path == "/api/health":
                return self._send(200, {"ok": True})
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            if self.path == "/api/me":
                return self._send(200, {"id": u.id, "name": u.name, "dept": u.dept, "level": u.level, "is_admin": "admin" in u.roles})
            if self.path == "/api/audit":
                if "admin" not in u.roles:
                    return self._send(403, {"error": "forbidden"})
                with lock:
                    ok, bad = app.store.verify_audit()
                    return self._send(200, {"chain_ok": ok, "first_bad_id": bad, "entries": app.store.audit_log()})
            self._send(404, {"error": "not found"})

        def _post(self):
            u = self._user()
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            if self.path != "/api/ask":
                return self._send(404, {"error": "not found"})
            if self._limited(u.id):
                return self._send(429, {"error": "rate limited"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                q = str(json.loads(self.rfile.read(min(n, 65536)))["question"]).strip()
                assert q
            except Exception:
                return self._send(400, {"error": "bad request"})
            with lock:
                result = app.engine.ask(u, q).to_dict()
            self._send(200, result)

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
