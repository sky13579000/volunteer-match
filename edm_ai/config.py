from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .engine import Engine
from .entities import EntityExtractor
from .dbdrivers import connect
from .gateway import SqlGateway
from .ingest import ingest_dir
from .llm import OpenAICompatClient
from .models import Principal
from .store import DocumentStore
from .vectors import OpenAICompatEmbedder


class App:
    """Wires config -> store, gateway, extractor, engine. All paths resolve relative to the config file."""

    def __init__(self, config_path: str):
        cp = Path(config_path).resolve()
        self.base = cp.parent
        self.lock = threading.RLock()  # serialises the shared DB connections (requests, re-indexing)
        self.cfg = json.loads(cp.read_text("utf-8"))
        store_path = os.environ.get("EDM_AI_STORE") or str(self._p(self.cfg.get("store", "store.db")))  # env wins: containers
        self.store = DocumentStore(store_path)
        s = self.cfg.get("structured")
        self.gateway = SqlGateway(connect(s, str(self.base)), s["tables"], s.get("metrics")) if s else None
        ent = self.cfg.get("entities", {})
        dicts = {d["type"]: self.gateway.distinct(d["table"], d["column"]) for d in ent.get("dictionaries", [])} \
            if self.gateway else {}
        self.extractor = EntityExtractor(ent.get("patterns"), dicts)
        llm = self.cfg.get("llm")
        emb = self.cfg.get("embedder")
        self.engine = Engine(self.store, self.gateway, self.extractor,
                             OpenAICompatClient(**llm) if llm else None,
                             embedder=OpenAICompatEmbedder(**emb) if emb else None,
                             pii_reveal_roles=self.cfg.get("pii", {}).get("reveal_roles", ()),
                             min_coverage=self.cfg.get("retrieval", {}).get("min_coverage", 0.2))
        self.users, self.emails, self.external = {}, {}, {}
        self._users_file = self._p(self.cfg.get("users", "users.json"))
        self._users_mtime = 0.0
        self._users_checked = 0.0
        self.reload_users()
        from .dms_all import Dms
        from .mailer import Mailer
        smtp = (self.cfg.get("notify") or {}).get("smtp")
        self.mailer = Mailer(smtp, self.cfg.get("notify", {}).get("base_url", "")) if smtp else None
        from .events import Events
        self.events = Events(self.cfg.get("events") or {})
        dcfg = self.cfg.get("dms", {})
        repo = self._p(dcfg["repo"]) if dcfg.get("repo") else Path(store_path).resolve().parent / "repo"
        self.dms = Dms(self, repo)
        self.dms.sync_crawled()
        self.dms.reindex_all()

    def reload_users(self) -> dict:
        """(Re)read users.json IN PLACE (other components hold references to these dicts). An invalid file keeps the old list."""
        raw = self._users_file.read_text("utf-8")
        users = json.loads(raw)
        if not isinstance(users, list) or not users:
            raise ValueError("users.json must be a non-empty list")
        new = {u["id"]: Principal.from_dict(u) for u in users}
        # e-mail addresses come from users.json ("email" or external.email); they are never returned by any API
        emails = {u["id"]: (u.get("email") or (u.get("external") or {}).get("email")) for u in users if u.get("email") or (u.get("external") or {}).get("email")}
        # external identities (Slack user id, e-mail ...) -> internal user; the ONLY way an adapter / SSO request gets a principal
        ext = {(k, str(v).lower() if k == "email" else str(v)): u["id"]
               for u in users for k, v in {**({"email": u["email"]} if u.get("email") else {}), **(u.get("external") or {})}.items()}
        for target, value in ((self.users, new), (self.emails, emails), (self.external, ext)):
            target.clear()
            target.update(value)
        self._users_mtime = self._users_file.stat().st_mtime
        return {"users": len(new)}

    def refresh_users(self, every: float = 5.0):
        """Cheap mtime poll (at most every `every` seconds): a `sync-users --apply` becomes live without a restart."""
        import logging
        import time
        now = time.time()
        if now - self._users_checked < every:
            return
        self._users_checked = now
        try:
            if self._users_file.stat().st_mtime != self._users_mtime:
                with self.lock:
                    log = self.reload_users()
                logging.getLogger("edm_ai").info("users.json reloaded: %s", log)
        except Exception:
            logging.getLogger("edm_ai").exception("users.json reload failed (keeping the previous list)")
            self._users_mtime = self._users_file.stat().st_mtime if self._users_file.exists() else 0.0

    def _p(self, rel: str) -> Path:
        return (self.base / rel).resolve()

    def ingest(self) -> dict:
        d = self.cfg["docs"]
        with self.lock:
            stats = ingest_dir(self._p(d["root"]), self._p(d["manifest"]) if d.get("manifest") else None,
                               self.store, self.extractor)
            self.dms.sync_crawled()
            if stats["added"] or stats["pruned"]:
                self.engine.reload()   # swaps the index only when something changed
        return stats
