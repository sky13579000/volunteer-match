from __future__ import annotations

import os
import time
from collections import Counter

from . import __version__

STARTED = time.time()


class SystemMixin:
    """Administrator views: who exists (organisation / roles / levels) and whether the platform is healthy."""

    def admin_users(self, p) -> dict:
        self._need_admin(p)
        docs = Counter(r["owner"] for r in self.q("SELECT owner FROM dms_docs WHERE origin='managed' AND deleted_at IS NULL"))
        pending = Counter(w["approver"] for w in self.q("SELECT approver FROM wf_steps WHERE status='pending'"))
        users = [{"id": u.id, "name": u.name, "dept": u.dept or "-", "roles": sorted(u.roles), "level": u.level, "has_email": u.id in self.app.emails,
                  "external_ids": sorted({k for (k, _), uid in self.app.external.items() if uid == u.id and k != "email"}),
                  "documents": docs.get(u.id, 0), "pending_approvals": pending.get(u.id, 0)} for u in self.app.users.values()]
        depts = Counter(u["dept"] for u in users)
        return {"users": sorted(users, key=lambda u: (u["dept"], u["id"])), "departments": [{"dept": k, "users": n} for k, n in sorted(depts.items())],
                "source": str(self.app._users_file.name), "hint": "조직도 변경은 `sync-users` 로 반영합니다 (docs/INTEGRATION.md)"}

    def system_status(self, p) -> dict:
        self._need_admin(p)
        app = self.app
        ok, bad = app.store.verify_audit()
        cfg = app.cfg
        adapters = cfg.get("adapters") or {}
        envset = lambda c: bool(c) and bool(os.environ.get(c.get("signing_secret_env") or c.get("secret_env") or "", ""))  # noqa: E731
        size = lambda path: os.path.getsize(path) if path and os.path.exists(path) else 0  # noqa: E731
        mail = app.mailer
        return {
            "version": __version__, "uptime_seconds": int(time.time() - STARTED), "auth_mode": (cfg.get("auth") or {}).get("mode", "header"),
            "index": {"documents": len(app.engine.index.docs), "chunks": len(app.engine.index.chunks), "embedder": bool(app.engine.embedder)},
            "llm": {"configured": bool(app.engine.llm), "model": getattr(app.engine.llm, "model", None)},
            "structured_db": {"connected": bool(app.gateway), "tables": sorted(app.gateway.tables) if app.gateway else []},
            "store_bytes": size(getattr(app.store, "path", None)),
            "audit": {"chain_ok": ok, "broken_at": bad, "entries": self.q1("SELECT COUNT(*) AS n FROM audit")["n"]},
            "adapters": {k: {"enabled": envset(v)} for k, v in adapters.items()},
            "mail": {"enabled": bool(mail), "sent": getattr(mail, "sent", 0), "failed": getattr(mail, "failed", 0), "queued": mail.q.qsize() if mail else 0},
            "external_share": self.share_cfg["enabled"],
            "queues": {"pending_access_requests": self.q1("SELECT COUNT(*) AS n FROM access_requests WHERE status='pending'")["n"],
                       "pending_workflows": self.q1("SELECT COUNT(*) AS n FROM workflows WHERE status='pending'")["n"]},
        }
