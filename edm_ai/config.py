from __future__ import annotations

import json
from pathlib import Path

from .engine import Engine
from .entities import EntityExtractor
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
        self.cfg = json.loads(cp.read_text("utf-8"))
        self.store = DocumentStore(str(self._p(self.cfg.get("store", "store.db"))))
        s = self.cfg.get("structured")
        self.gateway = SqlGateway(str(self._p(s["path"])), s["tables"], s.get("metrics")) if s else None
        ent = self.cfg.get("entities", {})
        dicts = {d["type"]: self.gateway.distinct(d["table"], d["column"]) for d in ent.get("dictionaries", [])} \
            if self.gateway else {}
        self.extractor = EntityExtractor(ent.get("patterns"), dicts)
        llm = self.cfg.get("llm")
        emb = self.cfg.get("embedder")
        self.engine = Engine(self.store, self.gateway, self.extractor,
                             OpenAICompatClient(**llm) if llm else None,
                             embedder=OpenAICompatEmbedder(**emb) if emb else None,
                             pii_reveal_roles=self.cfg.get("pii", {}).get("reveal_roles", ()))
        users = json.loads(self._p(self.cfg.get("users", "users.json")).read_text("utf-8"))
        self.users = {u["id"]: Principal.from_dict(u) for u in users}

    def _p(self, rel: str) -> Path:
        return (self.base / rel).resolve()

    def ingest(self) -> dict:
        d = self.cfg["docs"]
        stats = ingest_dir(self._p(d["root"]), self._p(d["manifest"]) if d.get("manifest") else None,
                           self.store, self.extractor)
        self.engine.reload()
        return stats
