from __future__ import annotations

import hashlib
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .models import Acl, Chunk, Document

# Bump whenever chunking / parsing / entity extraction changes, so unchanged files are re-processed.
PIPELINE_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, source TEXT UNIQUE, title TEXT, acl TEXT, sha TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  ord INTEGER, text TEXT, entities TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT, user_id TEXT, question TEXT,
  evidence TEXT, refused INTEGER, generator TEXT, prev TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS doc_meta(
  source TEXT PRIMARY KEY, owner TEXT, managed INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS acl_overrides(
  source TEXT PRIMARY KEY, acl TEXT, by_user TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS doc_history(
  id INTEGER PRIMARY KEY, source TEXT, ts TEXT, user_id TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS embeddings(
  chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE, model TEXT, vec TEXT, PRIMARY KEY(chunk_id, model));
"""


class DocumentStore:
    """EDM-side store: documents + chunks + ACLs in SQLite (swap for any DB; no server needed on-prem)."""

    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)  # callers serialise access (see server.py)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._migrate()

    def upsert(self, source: str, title: str, acl: Acl, pieces: list[tuple[str, frozenset]], raw: str) -> bool:
        """Returns False when unchanged (same content + ACL), so re-crawls are cheap and ACL changes propagate."""
        sha = hashlib.sha256((PIPELINE_VERSION + raw + json.dumps(acl.to_dict(), sort_keys=True)).encode()).hexdigest()
        row = self.db.execute("SELECT id, sha FROM documents WHERE source=?", (source,)).fetchone()
        if row and row[1] == sha:
            return False
        with self.db:
            if row:
                self.db.execute("DELETE FROM documents WHERE id=?", (row[0],))
            cur = self.db.execute("INSERT INTO documents(source,title,acl,sha) VALUES(?,?,?,?)",
                                  (source, title, json.dumps(acl.to_dict()), sha))
            self.db.executemany(
                "INSERT INTO chunks(doc_id,ord,text,entities) VALUES(?,?,?,?)",
                [(cur.lastrowid, i, t, json.dumps(sorted(e))) for i, (t, e) in enumerate(pieces)])
        return True

    def prune(self, keep_sources: set[str]) -> int:
        gone = [r[0] for r in self.db.execute("SELECT source FROM documents") if r[0] not in keep_sources]
        with self.db:
            self.db.executemany("DELETE FROM documents WHERE source=?", [(s,) for s in gone])
        return len(gone)

    def load(self) -> tuple[dict[int, Document], list[Chunk]]:
        docs = {r[0]: Document(r[0], r[1], r[2], Acl.from_dict(json.loads(r[3])))
                for r in self.db.execute("SELECT id,source,title,acl FROM documents")}
        chunks = [Chunk(r[0], r[1], r[2], r[3], frozenset(json.loads(r[4])))
                  for r in self.db.execute("SELECT id,doc_id,ord,text,entities FROM chunks ORDER BY id")]
        return docs, chunks

    def _migrate(self):
        """v0.1 -> v0.2: audit gains prev/hash. Legacy rows are chained retroactively (tamper evidence starts now)."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(audit)")}
        if "hash" in cols:
            return
        with self.db:
            self.db.execute("ALTER TABLE audit ADD COLUMN prev TEXT")
            self.db.execute("ALTER TABLE audit ADD COLUMN hash TEXT")
            prev = "genesis"
            for r in self.db.execute("SELECT id,ts,user_id,question,evidence,refused,generator FROM audit ORDER BY id").fetchall():
                h = self._digest(prev, r[1] or "", r[2], r[3], r[4], r[5], r[6])
                self.db.execute("UPDATE audit SET prev=?, hash=? WHERE id=?", (prev, h, r[0]))
                prev = h

    # ---- EDM metadata: ownership, ACL overrides (set from the UI), history ----
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def set_meta(self, source: str, owner: str, managed: bool):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO doc_meta(source,owner,managed,created) VALUES(?,?,?,COALESCE("
                            "(SELECT created FROM doc_meta WHERE source=?),?))",
                            (source, owner, int(managed), source, self._now()))

    def get_meta(self, source: str) -> dict:
        r = self.db.execute("SELECT owner,managed,created FROM doc_meta WHERE source=?", (source,)).fetchone()
        return {"owner": r[0], "managed": bool(r[1]), "created": r[2]} if r else {"owner": None, "managed": False, "created": None}

    def set_acl_override(self, source: str, acl: Acl, by: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO acl_overrides VALUES(?,?,?,?)",
                            (source, json.dumps(acl.to_dict()), by, self._now()))

    def acl_overrides(self) -> dict[str, Acl]:
        return {r[0]: Acl.from_dict(json.loads(r[1])) for r in self.db.execute("SELECT source, acl FROM acl_overrides")}

    def forget(self, source: str):
        with self.db:
            for t in ("doc_meta", "acl_overrides"):
                self.db.execute(f"DELETE FROM {t} WHERE source=?", (source,))
            self.db.execute("DELETE FROM documents WHERE source=?", (source,))

    def add_history(self, source: str, user_id: str, action: str, detail: str = ""):
        with self.db:
            self.db.execute("INSERT INTO doc_history(source,ts,user_id,action,detail) VALUES(?,?,?,?,?)",
                            (source, self._now(), user_id, action, detail))

    def history(self, source: str, limit: int = 50) -> list[dict]:
        return [{"ts": r[0], "user": r[1], "action": r[2], "detail": r[3]} for r in self.db.execute(
            "SELECT ts,user_id,action,detail FROM doc_history WHERE source=? ORDER BY id DESC LIMIT ?", (source, limit))]

    def get_embeddings(self, model: str) -> dict[int, list[float]]:
        return {r[0]: json.loads(r[1]) for r in
                self.db.execute("SELECT chunk_id, vec FROM embeddings WHERE model=?", (model,))}

    def put_embeddings(self, model: str, vecs: dict[int, list[float]]):
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)",
                                [(cid, model, json.dumps(v)) for cid, v in vecs.items()])

    @staticmethod
    def _digest(prev, ts, user_id, question, ev, refused, generator) -> str:
        return hashlib.sha256("\x1f".join([prev, ts, user_id, question, ev, str(refused), generator]).encode()).hexdigest()

    def log_audit(self, user_id: str, question: str, evidence_refs: list[str], refused: bool, generator: str):
        """Append-only, hash-chained: editing or deleting any past row breaks verify_audit()."""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ev = json.dumps(evidence_refs, ensure_ascii=False)
        with _AUDIT_LOCK, self.db:
            row = self.db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row[0] if row else "genesis"
            h = self._digest(prev, ts, user_id, question, ev, int(refused), generator)
            self.db.execute("INSERT INTO audit(ts,user_id,question,evidence,refused,generator,prev,hash) VALUES(?,?,?,?,?,?,?,?)",
                            (ts, user_id, question, ev, int(refused), generator, prev, h))

    def verify_audit(self) -> tuple[bool, int | None]:
        prev = "genesis"
        for r in self.db.execute("SELECT id,ts,user_id,question,evidence,refused,generator,prev,hash FROM audit ORDER BY id"):
            if r[7] != prev or r[8] != self._digest(prev, r[1], r[2], r[3], r[4], r[5], r[6]):
                return False, r[0]
            prev = r[8]
        return True, None

    def audit_log(self, limit: int = 100) -> list[dict]:
        cur = self.db.execute("SELECT ts,user_id,question,evidence,refused,generator FROM audit ORDER BY id DESC LIMIT ?",
                              (limit,))
        return [{"ts": r[0], "user": r[1], "question": r[2], "evidence": json.loads(r[3]),
                 "refused": bool(r[4]), "generator": r[5]} for r in cur]


_AUDIT_LOCK = threading.Lock()
