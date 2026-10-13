from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth
from .config import App

PAGE = """<!doctype html><meta charset=utf-8><title>EDM-AI</title>
<style>body{font:15px system-ui;max-width:820px;margin:2rem auto;padding:0 1rem}textarea{width:100%%;height:4rem}
pre{white-space:pre-wrap;background:#f5f5f5;padding:.8rem;border-radius:6px}.e{border-left:3px solid #888;padding-left:.6rem;margin:.6rem 0}
small{color:#666}</style>
<h2>EDM-AI 지식 플랫폼 (데모)</h2>
<label>사용자 <select id=u>%s</select></label>
<textarea id=q placeholder="예: 한빛전자 MES 계약 금액과 납기 이슈를 알려줘"></textarea>
<button onclick=ask()>질문</button><div id=out></div>
<script>
async function ask(){const r=await fetch('/api/ask',{method:'POST',headers:{'X-User-Id':u.value},body:JSON.stringify({question:q.value})});
const j=await r.json();const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
out.innerHTML='<pre>'+esc(j.answer||j.error)+'</pre>'+(j.evidence||[]).map(e=>'<div class=e><b>['+e.id+'] '+esc(e.title)+'</b> <small>'+esc(e.ref)+'</small><br>'+esc(e.text)+'</div>').join('')}
</script>"""


def make_handler(app: App, demo_login: bool):
    acfg = app.cfg.get("auth", {"mode": "header"})
    secret = os.environ.get(acfg.get("secret_env", "EDM_AI_SECRET"), "")
    if acfg["mode"] == "token" and len(secret) < 16:
        raise SystemExit("token auth needs env EDM_AI_SECRET (>=16 chars)")
    limit = int(app.cfg.get("rate_limit_per_min", 60))
    hits: dict[str, deque] = defaultdict(deque)
    lock = threading.Lock()  # SQLite connections are shared across request threads; serialise DB access

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj, ctype="application/json"):
            body = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
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
            if self.path == "/" and demo_login and acfg["mode"] == "header":
                opts = "".join(f"<option value={u.id}>{u.name} ({u.dept}/L{u.level})</option>" for u in app.users.values())
                return self._send(200, PAGE % opts, "text/html")
            u = self._user()
            if self.path == "/api/health":
                return self._send(200, {"ok": True})
            if not u:
                return self._send(401, {"error": "unauthenticated"})
            if self.path == "/api/audit":
                if "admin" not in u.roles:
                    return self._send(403, {"error": "forbidden"})
                with lock:
                    ok, bad = app.store.verify_audit()
                    return self._send(200, {"chain_ok": ok, "first_bad_id": bad, "entries": app.store.audit_log()})
            self._send(404, {"error": "not found"})

        def do_POST(self):
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


def serve(app: App, host="127.0.0.1", port=8080, demo_login=False):
    ThreadingHTTPServer((host, port), make_handler(app, demo_login)).serve_forever()
