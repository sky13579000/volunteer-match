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
        from .dms_all import Dms
        dcfg = self.cfg.get("dms", {})
        repo = self._p(dcfg["repo"]) if dcfg.get("repo") else Path(store_path).resolve().parent / "repo"
        self.dms = Dms(self, repo)
        self.dms.sync_crawled()
        self.dms.reindex_all()
        users = json.loads(self._p(self.cfg.get("users", "users.json")).read_text("utf-8"))
        self.users = {u["id"]: Principal.from_dict(u) for u in users}
        # external identities (Slack user id, e-mail ...) -> internal user; the ONLY way an adapter request gets a principal
        self.external = {(k, str(v)): u["id"] for u in users for k, v in (u.get("external") or {}).items()}

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
