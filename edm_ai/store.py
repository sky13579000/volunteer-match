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
MANAGED_PREFIX = "dms/"      # index sources of repository-managed documents; the folder crawler never touches them

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
CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY, parent_id INTEGER, name TEXT NOT NULL, owner TEXT, created TEXT,
  inherit INTEGER DEFAULT 1, entries TEXT DEFAULT '[]', origin TEXT DEFAULT 'managed', open_create INTEGER DEFAULT 0,
  UNIQUE(parent_id, name));
CREATE TABLE IF NOT EXISTS doc_types(
  code TEXT PRIMARY KEY, name TEXT, pattern TEXT, retention_years INTEGER DEFAULT 0,
  approval_required INTEGER DEFAULT 0, default_approvers TEXT DEFAULT '[]', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS dms_docs(
  id INTEGER PRIMARY KEY, doc_no TEXT UNIQUE, source TEXT UNIQUE, title TEXT, folder_id INTEGER,
  doc_type TEXT, origin TEXT DEFAULT 'managed', owner TEXT, created TEXT, updated TEXT,
  current_ver INTEGER DEFAULT 0, effective_ver INTEGER, status TEXT DEFAULT 'draft',
  tags TEXT DEFAULT '[]', props TEXT DEFAULT '{}', description TEXT DEFAULT '',
  level INTEGER DEFAULT 0, inherit INTEGER DEFAULT 1, entries TEXT DEFAULT '[]',
  approved_at TEXT, retention_until TEXT, legal_hold INTEGER DEFAULT 0,
  expires_at TEXT, expire_notified INTEGER DEFAULT 0,
  locked_by TEXT, locked_at TEXT, deleted_at TEXT, deleted_by TEXT, deleted_folder INTEGER, class_code TEXT);
CREATE INDEX IF NOT EXISTS ix_dms_folder ON dms_docs(folder_id);
CREATE TABLE IF NOT EXISTS dms_versions(
  id INTEGER PRIMARY KEY, doc_id INTEGER, ver INTEGER, filename TEXT, size INTEGER, sha256 TEXT,
  path TEXT, status TEXT DEFAULT 'draft', created_by TEXT, created TEXT, comment TEXT DEFAULT '',
  text TEXT DEFAULT '', UNIQUE(doc_id, ver));
CREATE TABLE IF NOT EXISTS workflows(
  id INTEGER PRIMARY KEY, doc_id INTEGER, ver INTEGER, requester TEXT, mode TEXT DEFAULT 'sequential',
  status TEXT DEFAULT 'pending', note TEXT DEFAULT '', created TEXT, completed TEXT);
CREATE TABLE IF NOT EXISTS wf_steps(
  id INTEGER PRIMARY KEY, wf_id INTEGER, ord INTEGER, approver TEXT, status TEXT DEFAULT 'pending',
  comment TEXT DEFAULT '', acted_at TEXT, delegated_from TEXT);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, doc_id INTEGER, user_id TEXT, text TEXT, ts TEXT, parent_id INTEGER, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id TEXT, kind TEXT, doc_id INTEGER, text TEXT, ts TEXT, read INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, read);
CREATE TABLE IF NOT EXISTS doc_links(a INTEGER, b INTEGER, created_by TEXT, ts TEXT, PRIMARY KEY(a, b));
CREATE TABLE IF NOT EXISTS access_requests(
  id INTEGER PRIMARY KEY, doc_id INTEGER, requester TEXT, role TEXT, reason TEXT DEFAULT '', status TEXT DEFAULT 'pending',
  decided_by TEXT, decided_at TEXT, note TEXT DEFAULT '', created TEXT, ref TEXT);
CREATE INDEX IF NOT EXISTS ix_access_status ON access_requests(status, requester);
CREATE TABLE IF NOT EXISTS classifications(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT, retention_years INTEGER DEFAULT 0, description TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS dlp_findings(
  doc_id INTEGER, ver INTEGER, kind TEXT, n INTEGER, ts TEXT, PRIMARY KEY(doc_id, ver, kind));
CREATE TABLE IF NOT EXISTS disposals(
  id INTEGER PRIMARY KEY, status TEXT DEFAULT 'pending', reason TEXT DEFAULT '', requested_by TEXT, requested_at TEXT,
  decided_by TEXT, decided_at TEXT, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS disposal_items(
  id INTEGER PRIMARY KEY, disposal_id INTEGER, doc_id INTEGER, doc_no TEXT, title TEXT, class_code TEXT, folder TEXT,
  owner TEXT, retention_until TEXT, doc_updated TEXT, result TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS disposal_ledger(
  id INTEGER PRIMARY KEY, disposal_id INTEGER, doc_no TEXT, title TEXT, class_code TEXT, folder TEXT, owner TEXT,
  retention_until TEXT, versions INTEGER, sha256 TEXT, disposed_at TEXT, requested_by TEXT, approved_by TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS backup_runs(
  id INTEGER PRIMARY KEY, ts TEXT, ok INTEGER, path TEXT, bytes INTEGER, files INTEGER, note TEXT, by TEXT);
CREATE TABLE IF NOT EXISTS type_fields(code TEXT PRIMARY KEY, fields TEXT);
CREATE TABLE IF NOT EXISTS approval_templates(
  id INTEGER PRIMARY KEY, name TEXT, owner TEXT, approvers TEXT, cc TEXT DEFAULT '[]', mode TEXT DEFAULT 'sequential', shared INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS absences(user_id TEXT PRIMARY KEY, deputy TEXT, frm TEXT, to_ TEXT, reason TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS trust_runs(
  id INTEGER PRIMARY KEY, ts TEXT, by TEXT, cases INTEGER, recall REAL, mrr REAL, leaks INTEGER, failures TEXT, answers TEXT);
CREATE TABLE IF NOT EXISTS doc_activity(
  id INTEGER PRIMARY KEY, doc_id INTEGER, user_id TEXT, action TEXT, ver INTEGER, out_sha TEXT, ts TEXT);
CREATE INDEX IF NOT EXISTS ix_act_doc ON doc_activity(doc_id, id);
CREATE INDEX IF NOT EXISTS ix_act_sha ON doc_activity(out_sha);
CREATE TABLE IF NOT EXISTS pii_registry(
  id INTEGER PRIMARY KEY, name TEXT, purpose TEXT, legal_basis TEXT DEFAULT '', items TEXT DEFAULT '', subjects TEXT DEFAULT '', retention TEXT DEFAULT '',
  third_party TEXT DEFAULT '', outsourced TEXT DEFAULT '', manager TEXT DEFAULT '', location TEXT DEFAULT '', destroy_by TEXT, folder_id INTEGER,
  status TEXT DEFAULT 'active', notified TEXT, created_by TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS pii_access_log(
  id INTEGER PRIMARY KEY, doc_id INTEGER, doc_no TEXT, user_id TEXT, action TEXT, reason TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS api_keys(
  id INTEGER PRIMARY KEY, name TEXT, user_id TEXT, key_hash TEXT UNIQUE, prefix TEXT, scope TEXT DEFAULT 'read',
  created_by TEXT, created TEXT, expires TEXT, last_used TEXT, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS source_runs(
  id INTEGER PRIMARY KEY, source TEXT, kind TEXT, ts TEXT, ok INTEGER, stats TEXT);
CREATE TABLE IF NOT EXISTS source_items(
  source TEXT, rel TEXT, status TEXT, doc_id INTEGER, sha256 TEXT, note TEXT, ts TEXT, PRIMARY KEY(source, rel));
CREATE TABLE IF NOT EXISTS mail_items(
  id INTEGER PRIMARY KEY, account TEXT, message_id TEXT, subject TEXT, sender TEXT, status TEXT, doc_ids TEXT, note TEXT, ts TEXT, UNIQUE(account, message_id));
CREATE TABLE IF NOT EXISTS follows(user_id TEXT, doc_id INTEGER, ts TEXT, PRIMARY KEY(user_id, doc_id));
CREATE TABLE IF NOT EXISTS user_prefs(user_id TEXT PRIMARY KEY, email INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS favorites(user_id TEXT, doc_id INTEGER, ts TEXT, PRIMARY KEY(user_id, doc_id));
CREATE TABLE IF NOT EXISTS recent(user_id TEXT, doc_id INTEGER, ts TEXT, PRIMARY KEY(user_id, doc_id));
CREATE TABLE IF NOT EXISTS saved_searches(id INTEGER PRIMARY KEY, user_id TEXT, name TEXT, query TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS shares(
  id INTEGER PRIMARY KEY, doc_id INTEGER, ver INTEGER, token_hash TEXT UNIQUE, created_by TEXT, created TEXT,
  expires TEXT, max_dl INTEGER DEFAULT 0, dl_count INTEGER DEFAULT 0, views INTEGER DEFAULT 0,
  pw_salt TEXT, pw_hash TEXT, perm TEXT DEFAULT 'view', revoked INTEGER DEFAULT 0,
  fails INTEGER DEFAULT 0, locked_until REAL DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS embeddings(
  chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE, model TEXT, vec TEXT, PRIMARY KEY(chunk_id, model));
"""


class DocumentStore:
    """EDM-side store: documents + chunks + ACLs in SQLite (swap for any DB; no server needed on-prem)."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
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

    def prune_sources(self, sources: list[str]) -> int:
        with self.db:
            self.db.executemany("DELETE FROM documents WHERE source=?", [(x,) for x in sources])
        return len(sources)

    def would_change(self, source: str, acl: Acl, raw: str) -> bool:
        sha = hashlib.sha256((PIPELINE_VERSION + raw + json.dumps(acl.to_dict(), sort_keys=True)).encode()).hexdigest()
        row = self.db.execute("SELECT sha FROM documents WHERE source=?", (source,)).fetchone()
        return not (row and row[0] == sha)

    def prune(self, keep_sources: set[str]) -> int:
        gone = [r[0] for r in self.db.execute("SELECT source FROM documents")
                if r[0] not in keep_sources and not r[0].startswith(MANAGED_PREFIX)]
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
        dcols = {r[1] for r in self.db.execute("PRAGMA table_info(dms_docs)")}
        if dcols and "class_code" not in dcols:
            with self.db:
                self.db.execute("ALTER TABLE dms_docs ADD COLUMN class_code TEXT")
        for tbl, col, typ in (("disposals", "method", "TEXT DEFAULT 'delete'"), ("disposal_ledger", "method", "TEXT DEFAULT 'delete'"), ("shares", "latest", "INTEGER DEFAULT 0"), ("workflows", "cc", "TEXT DEFAULT '[]'")):
            tc = {r[1] for r in self.db.execute(f"PRAGMA table_info({tbl})")}
            if tc and col not in tc:
                with self.db:
                    self.db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
        fcols = {r[1] for r in self.db.execute("PRAGMA table_info(folders)")}
        if fcols and "open_create" not in fcols:
            with self.db:
                self.db.execute("ALTER TABLE folders ADD COLUMN open_create INTEGER DEFAULT 0")
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

    def forget_index(self, source: str):
        """Remove a document from the search index only (managed documents keep their DMS rows)."""
        with self.db:
            self.db.execute("DELETE FROM documents WHERE source=?", (source,))

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
