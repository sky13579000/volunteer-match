from __future__ import annotations

import json
import logging
import re
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, adapters, auth
from .config import App
from .errors import DmsError
from .routes import Raw, build_routes

log = logging.getLogger("edm_ai")
STATIC = Path(__file__).parent / "static"
STATIC_NAME = re.compile(r"/([A-Za-z0-9_\-]{1,40})\.(js|css)")
PUBLIC_SHARE = re.compile(r"/s/[A-Za-z0-9_\-]{20,100}")
FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css"),
         "/share.js": ("share.js", "text/javascript")}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"


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
    gw_cfg = acfg_ad.get("groupware") or {}
    gw_secret = os.environ.get(gw_cfg.get("secret_env", ""), "") if gw_cfg else ""
    for name, cfg_, sec in (("slack", acfg_ad.get("slack"), slack_secret), ("webhook", acfg_ad.get("webhook"), hook_secret),
                            ("groupware", acfg_ad.get("groupware"), gw_secret)):
        if cfg_ and sec and len(sec) < 16:      # unset secret = adapter disabled; a WEAK secret is refused
            raise SystemExit(f"adapter '{name}': secret must be at least 16 characters")
        if cfg_ and not sec:
            log.info("adapter '%s' disabled (secret env var not set)", name)
    replay = adapters.ReplayGuard()
    trusted = set(acfg.get("trusted_proxies", []))
    if acfg["mode"] == "proxy" and not trusted:
        log.warning("auth.mode=proxy without trusted_proxies: ANY client that can reach this port can claim any identity. Bind to 127.0.0.1 or set trusted_proxies.")
    lock = app.lock  # DB connections are shared across request threads and the re-indexer
    routes = build_routes(app)
    dms = app.dms

    class H(BaseHTTPRequestHandler):
        # ---------- responses ----------
        def _headers(self, ctype, length, extra=()):
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()

        def _send(self, code, obj, ctype="application/json"):
            body = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self._headers(f"{ctype}; charset=utf-8", len(body))
            self.wfile.write(body)

        def _send_raw(self, r: Raw):
            """Downloads are ALWAYS attachments: an uploaded .html/.svg must never be rendered on our origin."""
            from urllib.parse import quote
            self.send_response(200)
            safe = re.sub(r'[^\w.\- ]', "_", r.filename, flags=re.ASCII) or "download"
            self._headers("application/octet-stream" if r.ctype in ("text/html", "image/svg+xml", "application/xhtml+xml") else r.ctype, len(r.data),
                          [("Content-Disposition", f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(r.filename)}")])
            self.wfile.write(r.data)

        # ---------- request helpers ----------
        def body(self, max_bytes: int) -> dict:
            try:
                n = int(self.headers.get("Content-Length", 0))
            except ValueError:
                raise DmsError(400, "bad request")
            if n > max_bytes:
                raise DmsError(413, "요청이 너무 큽니다")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
                assert isinstance(data, dict)
                return data
            except Exception:
                raise DmsError(400, "bad request")

        def _user(self):
            if acfg["mode"] == "token":
                tok = self.headers.get("Authorization", "")
                return app.users.get(auth.verify(secret, tok[7:]) or "") if tok.startswith("Bearer ") else None
            if acfg["mode"] == "proxy":
                # identity asserted by an authenticating reverse proxy (oauth2-proxy / Nginx + OIDC / SAML). Only accepted
                # from the configured proxy addresses; the e-mail (or id) must be mapped in users.json - unknown = 401.
                if trusted and self.client_address[0] not in trusted:
                    return None
                v = (self.headers.get(acfg.get("header", "X-Forwarded-Email"), "") or "").strip()
                if not v:
                    return None
                if acfg.get("match", "email") == "email":
                    return app.users.get(app.external.get(("email", v.lower()), ""))
                return app.users.get(v)
            # header mode: ONLY behind an authenticating proxy (SSO/mTLS) that strips client-supplied copies.
            return app.users.get(self.headers.get(acfg.get("header", "X-User-Id"), ""))

        def _limited(self, key, cap=None):
            now, q = time.time(), hits[key]
            while q and q[0] < now - 60:
                q.popleft()
            q.append(now)
            return len(q) > (cap or limit)

        def do_GET(self):
            self._safe(lambda: self._dispatch("GET"))

        def do_POST(self):
            self._safe(lambda: self._dispatch("POST"))

        def do_PUT(self):
            self._safe(lambda: self._dispatch("PUT"))

        def do_DELETE(self):
            self._safe(lambda: self._dispatch("DELETE"))

        def _safe(self, fn):
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except DmsError as e:
                try:
                    self._send(e.code, {"error": e.msg})
                except Exception:
                    pass
            except Exception:  # never drop the connection or leak internals
                log.exception("request failed: %s %s", self.command, self.path)
                try:
                    self._send(500, {"error": "internal error"})
                except Exception:
                    pass

        # ---------- routing ----------
        def _dispatch(self, method):
            url = urlparse(self.path)
            path, qs = url.path, parse_qs(url.query)
            if method == "GET":
                if path == "/":
                    return self._send(200, (STATIC / "index.html").read_text("utf-8"), "text/html")
                sm = STATIC_NAME.fullmatch(path)                         # only plain file names: no slashes, no traversal
                if sm and (STATIC / f"{sm.group(1)}.{sm.group(2)}").is_file():
                    return self._send(200, (STATIC / f"{sm.group(1)}.{sm.group(2)}").read_text("utf-8"),
                                      "text/javascript" if sm.group(2) == "js" else "text/css")
                if PUBLIC_SHARE.fullmatch(path):                       # /s/<token>: the public landing page (no login)
                    return self._send(200, (STATIC / "share.html").read_text("utf-8"), "text/html")
                if path == "/api/config":
                    return self._send(200, {"auth_mode": acfg["mode"], "demo": demo_login and acfg["mode"] == "header",
                                            "examples": app.cfg.get("examples", []), "version": __version__,
                                            "logout_url": acfg.get("logout_url", "") if acfg["mode"] == "proxy" else ""})
                if path == "/api/demo-users":
                    if not (demo_login and acfg["mode"] == "header"):
                        return self._send(404, {"error": "not found"})
                    return self._send(200, [{"id": x.id, "name": x.name, "dept": x.dept, "dept_name": app.cfg.get("departments", {}).get(x.dept, x.dept), "level": x.level} for x in app.users.values()])
                if path == "/api/health":
                    return self._send(200, {"ok": True})
            if path.startswith("/api/share/"):
                return self._public_share(method, path)
            if method == "POST" and path.startswith("/api/adapters/"):
                return self._adapter(path)
            app.refresh_users()
            u = self._user()
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            if method == "GET" and path == "/api/me":
                return self._send(200, {"id": u.id, "name": u.name, "dept": u.dept, "level": u.level, "is_admin": "admin" in u.roles})
            if method == "GET" and path == "/api/audit":
                if "admin" not in u.roles:
                    return self._send(403, {"error": "forbidden"})
                with lock:
                    ok, bad = app.store.verify_audit()
                    return self._send(200, {"chain_ok": ok, "first_bad_id": bad, "entries": app.store.audit_log()})
            if method == "POST" and path == "/api/ask":
                if self._limited(u.id):
                    return self._send(429, {"error": "rate limited"})
                b = self.body(65536)
                q = str(b.get("question", "")).strip()
                if not q:
                    raise DmsError(400, "bad request")
                with lock:
                    result = app.engine.ask(u, q).to_dict()
                    for e in result["evidence"]:
                        if e["kind"] == "doc":
                            r = dms.q1("SELECT id FROM dms_docs WHERE source=? AND deleted_at IS NULL", (e["ref"].split("#")[0],))
                            e["doc_id"] = r["id"] if r else None
                return self._send(200, result)
            for meth, rx, fn, mutating in routes:
                m = rx.fullmatch(path)
                if meth == method and m:
                    if mutating and self._limited(u.id):
                        return self._send(429, {"error": "rate limited"})
                    with lock:
                        result = fn(self, u, m, qs)
                    return self._send_raw(result) if isinstance(result, Raw) else self._send(200, result)
            return self._send(404, {"error": "not found"})

        # ---------- public share (no login; rate limited per client address) ----------
        def _public_share(self, method, path):
            if self._limited("ip:" + self.client_address[0], 40):
                return self._send(429, {"error": "rate limited"})
            m = re.fullmatch(r"/api/share/([A-Za-z0-9_\-]{20,100})(?:/(access|download))?", path)
            if not m:
                return self._send(404, {"error": "not found"})
            token, action = m.group(1), m.group(2)
            if method == "GET" and not action:
                with lock:
                    return self._send(200, dms.share_info(token))
            if method == "POST" and action:
                pw = self.body(4096).get("password")
                with lock:
                    if action == "access":
                        return self._send(200, dms.share_access(token, pw))
                    name, data, mime = dms.share_download(token, pw)
                return self._send_raw(Raw(data, name, mime))
            return self._send(404, {"error": "not found"})

        def _adapter(self, path: str):
            """Slack slash command / signed webhook. Authenticated by signature, never by the user headers."""
            n = int(self.headers.get("Content-Length", 0))
            cap = int(gw_cfg.get("max_mb", 50)) * 1048576 * 4 // 3 + 65536 if path == "/api/adapters/groupware" else 65536
            if n > cap:
                return self._send(413, {"error": "too large"})
            raw = self.rfile.read(n)
            if path == "/api/adapters/slack" and slack_secret:
                if not adapters.verify_slack(slack_secret, self.headers.get("X-Slack-Request-Timestamp", ""), raw,
                                             self.headers.get("X-Slack-Signature", "")):
                    return self._send(401, {"error": "bad signature"})
                cmd = adapters.parse_slash(raw)
                uid = app.external.get(("slack", cmd["user_id"]))
                if not uid:
                    return self._send(200, {"response_type": "ephemeral", "text": "이 Slack 계정은 플랫폼 사용자와 연결되어 있지 않습니다. 관리자에게 문의하세요."})
                if not cmd["text"]:
                    return self._send(200, {"response_type": "ephemeral", "text": "사용법: /edm 제주관광공사 계약 금액은?"})
                if self._limited(uid):
                    return self._send(200, {"response_type": "ephemeral", "text": "요청이 너무 많습니다. 잠시 후 다시 시도하세요."})
                with lock:
                    ans = app.engine.ask(app.users[uid], cmd["text"][:2000]).to_dict()
                return self._send(200, {"response_type": "ephemeral", "text": adapters.format_chat(ans)})   # ephemeral: only the asker sees it
            if path == "/api/adapters/groupware" and gw_secret:
                ts, sig = self.headers.get("X-EDM-Timestamp", ""), self.headers.get("X-EDM-Signature", "")
                if not adapters.verify_webhook(gw_secret, ts, raw, sig):
                    return self._send(401, {"error": "bad signature"})
                if not replay.fresh(sig):
                    return self._send(409, {"error": "replayed request"})
                try:
                    b = json.loads(raw)
                    assert isinstance(b, dict)
                except Exception:
                    return self._send(400, {"error": "bad request"})
                if self._limited("gw", 600):
                    return self._send(429, {"error": "rate limited"})
                from . import groupware
                try:
                    with lock:
                        res = groupware.ingest(app, gw_cfg, b)
                except DmsError as e:
                    return self._send(e.code, {"error": e.msg})
                return self._send(200 if res["ok"] else 207, res)
            if path == "/api/adapters/webhook" and hook_secret:
                ts, sig = self.headers.get("X-EDM-Timestamp", ""), self.headers.get("X-EDM-Signature", "")
                if not adapters.verify_webhook(hook_secret, ts, raw, sig):
                    return self._send(401, {"error": "bad signature"})
                if not replay.fresh(sig):
                    return self._send(409, {"error": "replayed request"})
                try:
                    b = json.loads(raw)
                    ident, q = b["user"], str(b["question"]).strip()[:2000]
                    uid = app.external.get((str(ident["type"]), str(ident["id"]).lower() if ident["type"] == "email" else str(ident["id"])))
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
            with app.lock:
                mt = app.dms.maintenance()
            if any(mt.values()):
                log.info("maintenance: %s", mt)
        except Exception:
            log.exception("reindex failed (serving previous index)")


def serve(app: App, host="127.0.0.1", port=8080, demo_login=False, ingest=False, reindex_every=0):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if ingest:
        log.info("startup ingest: %s", app.ingest())
    with app.lock:
        log.info("startup maintenance: %s", app.dms.maintenance())
    if reindex_every:
        threading.Thread(target=_reindex_loop, args=(app, reindex_every), daemon=True).start()
    ThreadingHTTPServer((host, port), make_handler(app, demo_login)).serve_forever()
