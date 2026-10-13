from __future__ import annotations

import hashlib
import json
import sqlite3

from .models import Acl, Chunk, Document

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, source TEXT UNIQUE, title TEXT, acl TEXT, sha TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  ord INTEGER, text TEXT, entities TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP, user_id TEXT, question TEXT,
  evidence TEXT, refused INTEGER, generator TEXT);
"""


class DocumentStore:
    """EDM-side store: documents + chunks + ACLs in SQLite (swap for any DB; no server needed on-prem)."""

    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)  # callers serialise access (see server.py)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def upsert(self, source: str, title: str, acl: Acl, pieces: list[tuple[str, frozenset]], raw: str) -> bool:
        """Returns False when unchanged (same content + ACL), so re-crawls are cheap and ACL changes propagate."""
        sha = hashlib.sha256((raw + json.dumps(acl.to_dict(), sort_keys=True)).encode()).hexdigest()
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

    def log_audit(self, user_id: str, question: str, evidence_refs: list[str], refused: bool, generator: str):
        with self.db:
            self.db.execute("INSERT INTO audit(user_id,question,evidence,refused,generator) VALUES(?,?,?,?,?)",
                            (user_id, question, json.dumps(evidence_refs, ensure_ascii=False), int(refused), generator))

    def audit_log(self, limit: int = 100) -> list[dict]:
        cur = self.db.execute("SELECT ts,user_id,question,evidence,refused,generator FROM audit ORDER BY id DESC LIMIT ?",
                              (limit,))
        return [{"ts": r[0], "user": r[1], "question": r[2], "evidence": json.loads(r[3]),
                 "refused": bool(r[4]), "generator": r[5]} for r in cur]
