from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import automation
from .errors import DmsError
from .ingest import SUPPORTED, read_file
from .models import Acl
from .safety import redact_pii
from .store import MANAGED_PREFIX

FOLLOW_TEXT = {"new_version": "새 버전이 올라왔습니다", "restored": "이전 버전이 복원되었습니다", "approved_step": "결재가 진행되었습니다", "rejected": "결재가 반려되었습니다",
               "commented": "새 댓글이 달렸습니다", "moved": "다른 폴더로 이동되었습니다", "acl_changed": "접근 권한이 변경되었습니다"}
from .text import chunk_text

ROLE_RANK = {"viewer": 1, "editor": 2, "manager": 3}
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".dll", ".js", ".vbs", ".ps1", ".jar", ".sh", ".apk", ".app", ".lnk"}
MAX_TEXT_CACHE = 2_000_000
RESTRICTED = {"draft", "in_review", "rejected"}          # states in which only editors/owner/approvers may read


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def add_days(iso: str, days: int) -> str:
    return (datetime.fromisoformat(iso) + timedelta(days=days)).isoformat(timespec="seconds")


class DmsCore:
    """Folders, documents, versions, permissions and indexing. Every public method takes the authenticated
    Principal and re-checks permission itself. Callers hold app.lock (the DB connection is shared)."""

    def __init__(self, app, repo: Path):
        self.app = app
        self.store = app.store
        self.db = app.store.db
        self.repo = Path(repo)
        self.repo.mkdir(parents=True, exist_ok=True)
        c = app.cfg.get("dms", {})
        self.max_upload = int(c.get("max_upload_mb", 50)) * 1024 * 1024
        self.trash_days = int(c.get("trash_days", 30))
        self.warn_days = int(c.get("expiry_warn_days", 30))
        self.share_cfg = {"enabled": False, "max_days": 30, "max_level": 1, **c.get("external_share", {})}
        self._defer = 0
        self._dirty = False
        self._snap = None
        self.bootstrap(c)

    # ---------------- tiny SQL helpers ----------------
    def q(self, sql: str, args=()) -> list[dict]:
        cur = self.db.execute(sql, args)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def q1(self, sql: str, args=()) -> dict | None:
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql: str, args=()):
        with self.db:
            return self.db.execute(sql, args)

    # ---------------- bootstrap ----------------
    def bootstrap(self, c: dict):
        types = c.get("doc_types") or [
            {"code": "GEN", "name": "일반 문서", "pattern": "GEN-{year}-{seq:04}", "retention_years": 0, "approval_required": False},
            {"code": "CT", "name": "계약서", "pattern": "CT-{year}-{seq:04}", "retention_years": 10, "approval_required": True},
            {"code": "MN", "name": "회의록", "pattern": "MN-{year}-{seq:04}", "retention_years": 3, "approval_required": False},
            {"code": "RPT", "name": "보고서", "pattern": "RPT-{year}-{seq:04}", "retention_years": 5, "approval_required": True},
        ]
        if not self.q1("SELECT 1 AS x FROM doc_types LIMIT 1"):
            for t in types:
                self.x("INSERT OR IGNORE INTO doc_types VALUES(?,?,?,?,?,?,1)",
                       (t["code"], t["name"], t["pattern"], int(t.get("retention_years", 0)),
                        int(bool(t.get("approval_required"))), json.dumps(t.get("default_approvers", []))))
        if not self.q1("SELECT 1 AS x FROM folders LIMIT 1"):
            # anyone may REGISTER here (open_create) but the folder grants no read access: documents default to the author's department
            self.x("INSERT INTO folders(id,parent_id,name,owner,created,entries,origin,open_create) VALUES(1,NULL,'미분류','system',?,'[]','managed',1)", (now(),))
            self.x("INSERT INTO folders(id,parent_id,name,owner,created,entries,origin) VALUES(2,NULL,'외부 소스','system',?,'[]','crawled')", (now(),))
        for f in c.get("folders", []):
            self.ensure_folder_path(f["path"], f.get("entries"))

    def ensure_folder_path(self, path: str, entries=None, origin="managed", owner="system") -> int:
        parent = None
        for i, part in enumerate([x for x in path.split("/") if x.strip()]):
            part = part.strip()
            row = self.q1("SELECT id FROM folders WHERE name=? AND parent_id IS ?", (part, parent))
            if not row:
                last = i == len([x for x in path.split("/") if x.strip()]) - 1
                cur = self.x("INSERT INTO folders(parent_id,name,owner,created,entries,origin) VALUES(?,?,?,?,?,?)",
                             (parent, part, owner, now(), json.dumps(self._norm_entries(entries) if last and entries else []), origin))
                row = {"id": cur.lastrowid}
            parent = row["id"]
        return parent

    # ---------------- helpers ----------------
    @staticmethod
    def admin(p) -> bool:
        return "admin" in p.roles

    def batch(self):
        """Defer the (expensive) index reload until a group of changes is done."""
        core = self

        class _B:
            def __enter__(s):
                core._defer += 1

            def __exit__(s, *a):
                core._defer -= 1
                if core._defer == 0 and core._dirty:
                    core._dirty = False
                    core.app.engine.reload()
        return _B()

    def _changed(self):
        if self._defer:
            self._dirty = True
        else:
            self.app.engine.reload()

    @staticmethod
    def _norm_entries(raw) -> list[dict]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > 200:
            raise DmsError(400, "권한 목록 형식이 올바르지 않습니다")
        out, seen = [], set()
        for e in raw:
            try:
                t, i = str(e["type"]), str(e.get("id", ""))[:64]
                r = str(e.get("role", "viewer"))
            except (TypeError, KeyError, AttributeError):
                raise DmsError(400, "권한 항목 형식이 올바르지 않습니다")
            if t not in ("user", "dept", "role", "all", "deny") or (t != "all" and not i) or (t != "deny" and r not in ROLE_RANK):
                raise DmsError(400, f"권한 항목이 올바르지 않습니다: {t}/{i}/{r}")
            key = (t, i)
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": t, "id": "*" if t == "all" else i, **({} if t == "deny" else {"role": r})})
        return out

    @contextlib.contextmanager
    def snapshot(self):
        """Read-only fast path for list-style requests: folders, approvers, doc types and every document row are loaded
        ONCE instead of per document (search / tree / dashboard went from O(N) queries per document to O(1)).
        Never use around writes."""
        if self._snap is not None:
            yield
            return
        folders = {}
        for r in self.q("SELECT * FROM folders"):
            r["entries"] = json.loads(r["entries"])
            folders[r["id"]] = r
        appr: dict = {}
        for r in self.q("SELECT w.doc_id AS d, s.approver AS a FROM wf_steps s JOIN workflows w ON w.id=s.wf_id WHERE w.status='pending' AND s.status='pending'"):
            appr.setdefault(r["d"], set()).add(r["a"])
        rows = {r["id"]: self._parse_row(r) for r in self.q("SELECT * FROM dms_docs")}
        self._snap = {"folders": folders, "appr": appr, "rows": rows, "types": {t["code"]: t["name"] for t in self.q("SELECT code,name FROM doc_types")},
                      "paths": {}}
        try:
            yield
        finally:
            self._snap = None

    def all_rows(self, include_deleted=False) -> list[dict]:
        with self.snapshot():
            return [r for r in self._snap["rows"].values() if include_deleted or not r["deleted_at"]]

    @staticmethod
    def _parse_row(r: dict) -> dict:
        for k in ("tags", "props", "entries"):
            r[k] = json.loads(r[k] or ("{}" if k == "props" else "[]"))
        return r

    def folder(self, fid) -> dict | None:
        if self._snap is not None:
            return self._snap["folders"].get(fid)
        r = self.q1("SELECT * FROM folders WHERE id=?", (fid,))
        if r:
            r["entries"] = json.loads(r["entries"])
        return r

    def folder_chain(self, fid) -> list[dict]:
        out = []
        while fid is not None and len(out) < 50:
            f = self.folder(fid)
            if not f:
                break
            out.append(f)
            fid = f["parent_id"]
        return out

    def folder_path(self, fid) -> str:
        if self._snap is not None and fid in self._snap["paths"]:
            return self._snap["paths"][fid]
        path = " / ".join(f["name"] for f in reversed(self.folder_chain(fid)))
        if self._snap is not None:
            self._snap["paths"][fid] = path
        return path

    def _folder_entries(self, fid) -> list[dict]:
        entries = []
        for f in self.folder_chain(fid):
            entries += f["entries"]
            if not f["inherit"]:
                break
        return entries

    def row(self, doc_id) -> dict | None:
        if self._snap is not None:
            return self._snap["rows"].get(doc_id)
        r = self.q1("SELECT * FROM dms_docs WHERE id=?", (doc_id,))
        return self._parse_row(r) if r else None

    def _eff_entries(self, row) -> list[dict]:
        entries = list(row["entries"])
        if row["inherit"] and row["folder_id"] is not None:
            entries = self._folder_entries(row["folder_id"]) + entries
        return entries

    @staticmethod
    def _match(p, e) -> bool:
        t = e["type"]
        return t == "all" or (t == "user" and e["id"] == p.id) or (t == "dept" and e["id"] == p.dept) or (t == "role" and e["id"] in p.roles)

    def role_of(self, p, row) -> int:
        """0 none, 1 viewer, 2 editor, 3 manager. The owner is always manager."""
        if row["owner"] == p.id:
            return 3
        best = 0
        for e in self._eff_entries(row):
            if e["type"] != "deny" and self._match(p, e):
                best = max(best, ROLE_RANK[e["role"]])
        return best

    def approvers_of(self, doc_id) -> set[str]:
        if self._snap is not None:
            return self._snap["appr"].get(doc_id, set())
        return {r["approver"] for r in self.q(
            "SELECT s.approver FROM wf_steps s JOIN workflows w ON w.id=s.wf_id WHERE w.doc_id=? AND w.status='pending' AND s.status='pending'", (doc_id,))}

    def derive_acl(self, row) -> Acl:
        """READ access, derived from entries + lifecycle state. The index stores this, so search, AI answers and
        the document list can never disagree. Unpublished (draft / in review / rejected), expired documents are
        readable only by owner, editors/managers and the current approvers."""
        entries = self._eff_entries(row)
        denies = tuple(sorted({e["id"] for e in entries if e["type"] == "deny"}))
        grants = [e for e in entries if e["type"] != "deny"]
        published = row["effective_ver"] is not None and row["status"] not in ("expired",) and not row["deleted_at"]
        if published:
            if not grants or any(e["type"] == "all" for e in grants):
                return Acl((), (), (), denies, row["level"])
            users = {e["id"] for e in grants if e["type"] == "user"} | {row["owner"]}
            return Acl(tuple(sorted({e["id"] for e in grants if e["type"] == "role"})),
                       tuple(sorted({e["id"] for e in grants if e["type"] == "dept"})), tuple(sorted(users)), denies, row["level"])
        ed = [e for e in grants if ROLE_RANK[e["role"]] >= 2 and e["type"] != "all"]
        users = {e["id"] for e in ed if e["type"] == "user"} | {row["owner"]} | self.approvers_of(row["id"])
        return Acl(tuple(sorted({e["id"] for e in ed if e["type"] == "role"})),
                   tuple(sorted({e["id"] for e in ed if e["type"] == "dept"})), tuple(sorted(users)), denies, row["level"])

    def idx_doc(self, row):
        return self.app.engine.index.by_source.get(row["source"])

    def can_read(self, p, row) -> bool:
        """Managed documents are judged from their CURRENT state (the very function that feeds the index), never from a
        possibly stale in-memory index: inside a batch the index reload is deferred. Crawled files use the index."""
        if row["deleted_at"]:
            return False
        if row["origin"] == "managed":
            return self.derive_acl(row).allows(p)
        d = self.idx_doc(row)
        return bool(d and d.acl.allows(p))

    def perms(self, p, row) -> set[str]:
        out: set[str] = set()
        crawled = row["origin"] == "crawled"
        readable = self.can_read(p, row)
        if readable:
            out |= {"read", "comment"}
            if not row["props"].get("no_download"):
                out.add("download")
        if self.admin(p):
            out |= {"manage", "delete"}
        if crawled:
            return out
        role = self.role_of(p, row)
        if readable or role >= 2:
            if role >= 2 and readable:
                out |= {"edit"}
            if role >= 3 and readable:
                out |= {"delete", "manage", "share"}
        if row["owner"] == p.id:
            out.add("manage")
        if p.id in self.approvers_of(row["id"]):
            out.add("approve")
        if "manage" in out and (self.admin(p) or role >= 3):
            out.add("acl")
        return out

    def get_row(self, p, doc_id, need: str = "read", allow_deleted=False) -> dict:
        row = self.row(doc_id)
        if not row or (row["deleted_at"] and not allow_deleted):
            raise DmsError(404, "문서를 찾을 수 없습니다")
        pr = self.perms(p, row)
        if need not in pr and not (need in ("manage", "acl") and "manage" in pr):
            if "read" not in pr and "manage" not in pr:
                raise DmsError(404, "문서를 찾을 수 없습니다")          # existence is never leaked
            raise DmsError(403, "이 작업을 수행할 권한이 없습니다")
        if need == "read" and "read" not in pr:
            raise DmsError(404, "문서를 찾을 수 없습니다")
        return row

    # ---------------- doc types / numbering ----------------
    def doc_type(self, code: str) -> dict:
        t = self.q1("SELECT * FROM doc_types WHERE code=?", (code,))
        if not t or not t["active"]:
            raise DmsError(400, f"알 수 없는 문서유형입니다: {code}")
        t["default_approvers"] = json.loads(t["default_approvers"] or "[]")
        t["approval_required"] = bool(t["approval_required"])
        return t

    def next_no(self, t: dict) -> str:
        year = datetime.now(timezone.utc).year
        key = f"{t['code']}-{year}"
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO counters VALUES(?,0)", (key,))
            self.db.execute("UPDATE counters SET n=n+1 WHERE key=?", (key,))
            n = self.db.execute("SELECT n FROM counters WHERE key=?", (key,)).fetchone()[0]
        return t["pattern"].format(year=year, seq=n)

    # ---------------- validation ----------------
    @staticmethod
    def clean_name(filename) -> str:
        name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "_", Path(str(filename)).name).strip()
        if not name or name.startswith(".") or len(name) > 150:
            raise DmsError(400, "파일 이름이 올바르지 않습니다")
        if Path(name).suffix.lower() in BLOCKED_EXT:
            raise DmsError(400, "보안상 올릴 수 없는 파일 형식입니다")
        return name

    def check_size(self, data: bytes):
        if not data:
            raise DmsError(400, "빈 파일은 올릴 수 없습니다")
        if len(data) > self.max_upload:
            raise DmsError(413, f"파일은 {self.max_upload // 1048576}MB 이하여야 합니다")

    @staticmethod
    def clean_tags(tags) -> list[str]:
        if tags is None:
            return []
        if not isinstance(tags, list):
            raise DmsError(400, "태그 형식이 올바르지 않습니다")
        out = []
        for t in tags[:20]:
            t = str(t).strip()[:40]
            if t and t not in out:
                out.append(t)
        return out

    @staticmethod
    def clean_props(props) -> dict:
        if props is None:
            return {}
        if not isinstance(props, dict) or len(props) > 30:
            raise DmsError(400, "속성 형식이 올바르지 않습니다")
        return {str(k)[:60]: str(v)[:200] for k, v in props.items()}

    @staticmethod
    def clean_date(v, field="날짜") -> str | None:
        if v in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(v)[:10]).date().isoformat()
        except ValueError:
            raise DmsError(400, f"{field} 형식은 YYYY-MM-DD 입니다")

    def clean_level(self, p, level) -> int:
        try:
            level = int(level or 0)
        except (TypeError, ValueError):
            raise DmsError(400, "보안등급이 올바르지 않습니다")
        if not 0 <= level <= 99:
            raise DmsError(400, "보안등급은 0~99 입니다")
        if level > p.level and not self.admin(p):
            raise DmsError(400, "본인 보안등급보다 높은 등급은 지정할 수 없습니다")
        return level

    # ---------------- file storage ----------------
    def _store_file(self, doc_id: int, ver: int, name: str, data: bytes) -> tuple[str, str]:
        d = self.repo / str(doc_id) / str(ver)
        d.mkdir(parents=True, exist_ok=True)
        dest = (d / name).resolve()
        if self.repo.resolve() not in dest.parents:
            raise DmsError(400, "파일 경로가 올바르지 않습니다")
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        return str(dest.relative_to(self.repo)), hashlib.sha256(data).hexdigest()

    def _extract(self, rel: str, name: str) -> tuple[str, str]:
        """(title, text) for supported formats; binary files are stored without text."""
        if Path(name).suffix.lower() not in SUPPORTED:
            return Path(name).stem, ""
        try:
            title, text = read_file(self.repo / rel)
        except Exception as e:                   # corrupt / unsupported content
            raise DmsError(422, f"문서를 읽을 수 없습니다: {e}")
        return title, text[:MAX_TEXT_CACHE]

    def read_bytes(self, ver: dict) -> bytes:
        data = (self.repo / ver["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != ver["sha256"]:
            raise DmsError(500, "저장된 파일의 무결성 검사에 실패했습니다")
        return data

    # ---------------- indexing ----------------
    def reindex(self, row, reload=True):
        src = row["source"]
        if row["origin"] == "crawled":
            return
        if row["deleted_at"]:
            self.store.forget_index(src)
            if reload:
                self._changed()
            return
        ver = row["effective_ver"] or row["current_ver"]
        v = self.q1("SELECT text FROM dms_versions WHERE doc_id=? AND ver=?", (row["id"], ver)) if ver else None
        text = (v or {}).get("text", "")
        if row["status"] == "expired":
            text = ""            # an expired document's CONTENT must not feed search or AI answers, even for its owner
        meta = f"{row['title']}\n{row['doc_no']} {row['doc_type']}\n{row['description']}\n{' '.join(row['tags'])}\n" + \
               "\n".join(f"{k}: {x}" for k, x in row["props"].items())
        pieces = [(meta.strip(), self.app.extractor.extract(meta))]
        pieces += [(c, self.app.extractor.extract(f"{row['title']}\n{c}")) for c in chunk_text(text)]
        self.store.upsert(src, row["title"], self.derive_acl(row), pieces, f"{meta}\n{text}" + ("\n[expired]" if row["status"] == "expired" else ""))
        if reload:
            self._changed()

    def reindex_doc(self, doc_id, reload=True):
        row = self.row(doc_id)
        if row:
            self.reindex(row, reload)

    def reindex_folder_tree(self, fid):
        """Inheritance changed: recompute every managed document below a folder."""
        ids = [fid]
        for _ in range(60):
            kids = [r["id"] for r in self.q(f"SELECT id FROM folders WHERE parent_id IN ({','.join('?' * len(ids))})", ids)] if ids else []
            new = [k for k in kids if k not in ids]
            if not new:
                break
            ids += new
        with self.batch():
            for d in self.q(f"SELECT id FROM dms_docs WHERE folder_id IN ({','.join('?' * len(ids))}) AND origin='managed'", ids):
                self.reindex_doc(d["id"])
            self._changed()

    # ---------------- history / audit ----------------
    def log(self, p, row, action: str, detail: str = ""):
        uid = p if isinstance(p, str) else p.id
        self.store.add_history(row["source"], uid, action, detail)
        self.store.log_audit(uid, f"[{action}] {row['doc_no'] or row['source']}", [row["source"]], False, "dms")
        if action in FOLLOW_TEXT and row["origin"] == "managed":
            self.notify_followers(row, FOLLOW_TEXT[action], uid)

    # ---------------- summaries ----------------
    def summary(self, p, row, with_perms=True) -> dict:
        t = {"name": self._snap["types"].get(row["doc_type"], row["doc_type"])} if self._snap is not None else (self.q1("SELECT name FROM doc_types WHERE code=?", (row["doc_type"],)) or {})
        readable = self.can_read(p, row)
        d = {"id": row["id"], "doc_no": row["doc_no"], "title": row["title"], "folder_id": row["folder_id"],
             "folder_path": self.folder_path(row["folder_id"]) if row["folder_id"] else "", "doc_type": row["doc_type"],
             "doc_type_name": t.get("name", row["doc_type"]), "status": row["status"], "origin": row["origin"],
             "owner": row["owner"], "created": row["created"], "updated": row["updated"], "current_ver": row["current_ver"],
             "effective_ver": row["effective_ver"], "tags": row["tags"], "level": row["level"], "readable": readable,
             "retention_until": row["retention_until"], "legal_hold": bool(row["legal_hold"]), "expires_at": row["expires_at"],
             "locked_by": row["locked_by"], "source": row["source"], "description": row["description"] if readable else ""}
        if not readable:
            d["tags"] = []
        if with_perms:
            d["perms"] = sorted(self.perms(p, row))
        return d
