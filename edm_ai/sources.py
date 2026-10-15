"""Storage connectors (NAS / file servers / FTP / WebDAV) - "virtual centralisation" and migration.

config.json:
  "sources": [
    {"name": "본부NAS", "type": "path", "path": "\\\\nas01\\share\\문서",      # UNC, mounted drive or NFS mount point
     "mode": "reference",                                                     # leave the files where they are, index + link them
     "include": ["*.docx", "*.hwp", "*.pdf"], "exclude": ["~$*", "*.tmp", "Thumbs.db"], "max_mb": 100,
     "acl": {"depts": ["mice"], "level": 1}, "acl_rules": [{"glob": "인사/*", "acl": {"roles": ["hr"], "level": 3}}],
     "schedule_minutes": 60},
    {"name": "이관NAS", "type": "path", "path": "/mnt/nas", "mode": "migrate", "folder": "NAS이관", "owner": "admin",
     "doc_type": "GEN", "folder_entries": [{"type": "dept", "id": "mice", "role": "viewer"}], "on_delete": "flag"},
    {"name": "협력사FTP", "type": "ftp", "host": "ftp.example", "port": 21, "tls": true, "user_env": "FTP_USER", "password_env": "FTP_PASS", "path": "/share"},
    {"name": "공유WebDAV", "type": "webdav", "url": "https://nas.example/webdav/", "user_env": "DAV_USER", "password_env": "DAV_PASS"} ]

reference: files stay on the storage; documents appear under "외부 소스/<name>/..." (read-only, always the file as it is NOW), searchable by
           the AI with the ACL you configure. FTP/WebDAV are mirrored into a local cache first (incremental, size + mtime).
migrate:   every file is copied into the managed repository as a real document (owner, folder tree, ACL, versions); later runs add new
           versions for changed files, skip duplicates (SHA-256) and report problems per file. Source files are never modified or deleted.
"""
from __future__ import annotations

import fnmatch
import ftplib
import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .errors import DmsError
from .ingest import Root, SUPPORTED
from .models import Acl

log = logging.getLogger("edm_ai")
NAME_RE = re.compile(r"[0-9A-Za-z가-힣_.-]{1,40}")
DEFAULT_EXCLUDE = ["~$*", "*.tmp", "*.temp", "Thumbs.db", "desktop.ini", ".DS_Store", "*.lnk", "*.bak", ".~lock*"]
BLOCKED = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".dll", ".js", ".vbs", ".ps1", ".jar", ".sh", ".apk", ".app", ".lnk"}


class SourceError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_segment(seg: str) -> str | None:
    seg = seg.strip()
    if not seg or seg in (".", "..") or "/" in seg or "\\" in seg or ":" in seg or "\x00" in seg:
        return None
    return seg


def _match(rel: str, pats) -> bool:
    low = rel.lower()
    base = low.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(low, p.lower()) or fnmatch.fnmatch(base, p.lower()) for p in pats)


class SourceManager:
    def __init__(self, app):
        self.app = app
        self.cfgs: dict[str, dict] = {}
        self.reload()
        self.cache_dir = Path(app.store.path).resolve().parent / "source_cache" if app.store.path != ":memory:" else Path(os.environ.get("TMPDIR", "/tmp")) / "edm_source_cache"
        self.running: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------------- config ----------------
    def reload(self):
        self.cfgs = {}
        for c in self.app.cfg.get("sources", []) or []:
            self._validate(c)
            self.cfgs[c["name"]] = c

    @staticmethod
    def _validate(c: dict):
        if not NAME_RE.fullmatch(str(c.get("name", ""))):
            raise SystemExit(f"sources: invalid name {c.get('name')!r} (letters, digits, Korean, _ . - up to 40)")
        t = c.get("type", "path")
        if t not in ("path", "ftp", "webdav"):
            raise SystemExit(f"sources[{c['name']}]: type must be path, ftp or webdav")
        if c.get("mode", "reference") not in ("reference", "migrate"):
            raise SystemExit(f"sources[{c['name']}]: mode must be reference or migrate")
        if t == "path" and not c.get("path"):
            raise SystemExit(f"sources[{c['name']}]: path is required")
        if t == "ftp" and not c.get("host"):
            raise SystemExit(f"sources[{c['name']}]: host is required")
        if t == "webdav" and not str(c.get("url", "")).startswith(("http://", "https://")):
            raise SystemExit(f"sources[{c['name']}]: url must be http(s)")
        if c.get("mode") == "migrate" and c.get("owner") is None:
            c["owner"] = "admin"

    def names(self) -> list[str]:
        return list(self.cfgs)

    def _acl(self, c: dict) -> Acl:
        return Acl.from_dict(c.get("acl", {"roles": ["admin"], "level": 99}))        # fail closed: admin only unless configured

    # ---------------- local view of a source ----------------
    def local_path(self, c: dict) -> Path:
        if c.get("type", "path") == "path":
            pth = Path(c["path"])
            return pth if pth.is_absolute() or str(c["path"]).startswith("\\\\") else self.app._p(c["path"])      # relative = next to config.json
        return self.cache_dir / c["name"]

    def reference_roots(self, mirror=True) -> list[Root]:
        """Crawl roots of all reference sources. `mirror` = True (all), False (none) or a set of names whose FTP/WebDAV cache is refreshed first.
        A mirror failure keeps serving the last good cache (and records the error) - an offline server never empties the index."""
        roots = []
        for name, c in self.cfgs.items():
            if c.get("mode", "reference") != "reference":
                continue
            if c.get("type", "path") != "path" and (mirror is True or (isinstance(mirror, (set, list, tuple)) and name in mirror)):
                try:
                    self.mirror(c)
                except Exception as e:
                    self._record(name, "mirror", {"error": f"{type(e).__name__}: {str(e)[:200]}"}, ok=False)
            roots.append(Root(self.local_path(c), f"{name}/", [{"glob": r["glob"], "acl": r["acl"]} for r in c.get("acl_rules", [])], self._acl(c),
                              c.get("include"), c.get("exclude", DEFAULT_EXCLUDE), int(c.get("max_mb", 100)) * 1048576, name))
        return roots

    def crawled_path(self, source: str) -> Path | None:
        """Where a crawled document's file lives (used by download). None for unknown prefixes."""
        for name, c in self.cfgs.items():
            if c.get("mode", "reference") == "reference" and source.startswith(name + "/"):
                base = self.local_path(c).resolve()
                p = (base / source[len(name) + 1:]).resolve()
                return p if base == p or base in p.parents else None
        return None

    # ---------------- run log ----------------
    def _record(self, name: str, kind: str, stats: dict, ok: bool = True):
        self.app.store.db.execute("INSERT INTO source_runs(source,kind,ts,ok,stats) VALUES(?,?,?,?,?)", (name, kind, now(), int(ok), json.dumps(stats, ensure_ascii=False)))
        self.app.store.db.commit()

    def status(self) -> list[dict]:
        out = []
        for name, c in self.cfgs.items():
            last = self.app.store.db.execute("SELECT ts,ok,stats,kind FROM source_runs WHERE source=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            docs = self.app.store.db.execute("SELECT COUNT(*) FROM documents WHERE source LIKE ?", (name + "/%",)).fetchone()[0] if c.get("mode", "reference") == "reference" else \
                self.app.store.db.execute("SELECT COUNT(*) FROM source_items WHERE source=? AND status IN ('migrated','updated','unchanged')", (name,)).fetchone()[0]
            out.append({"name": name, "type": c.get("type", "path"), "mode": c.get("mode", "reference"), "where": (c.get("path") or c.get("url") or f"{c.get('host')}:{c.get('path', '/')}") if c.get("type") != "ftp" else f"ftp://{c['host']}{c.get('path', '/')}",
                        "schedule_minutes": c.get("schedule_minutes"), "documents": docs, "running": name in self.running,
                        "last": {"ts": last[0], "ok": bool(last[1]), "kind": last[3], "stats": json.loads(last[2])} if last else None})
        return out

    def due(self) -> list[str]:
        out = []
        for name, c in self.cfgs.items():
            m = c.get("schedule_minutes")
            if not m or name in self.running:
                continue
            last = self.app.store.db.execute("SELECT ts FROM source_runs WHERE source=? AND kind IN ('sync','migrate') ORDER BY id DESC LIMIT 1", (name,)).fetchone()
            if not last or datetime.fromisoformat(last[0]) < datetime.now(timezone.utc) - timedelta(minutes=int(m)):
                out.append(name)
        return out

    # ---------------- mirroring (FTP / WebDAV) ----------------
    def mirror(self, c: dict) -> dict:
        dest = self.local_path(c)
        dest.mkdir(parents=True, exist_ok=True)
        state_file = dest / ".edm_state.json"
        try:
            state = json.loads(state_file.read_text("utf-8"))
        except (OSError, ValueError):
            state = {}
        t = c["type"]
        remote = (_list_ftp(c) if t == "ftp" else _list_webdav(c))
        include, exclude = c.get("include"), c.get("exclude", DEFAULT_EXCLUDE)
        limit, max_files = int(c.get("max_mb", 100)) * 1048576, int(c.get("max_files", 50000))
        stats = {"downloaded": 0, "unchanged": 0, "removed": 0, "skipped": 0}
        keep: set = set()
        wanted = []
        for rel, size, mtime in remote[:max_files]:
            segs = [_clean_segment(x) for x in rel.split("/")]
            if any(s is None for s in segs):
                stats["skipped"] += 1
                continue
            rel = "/".join(segs)
            if (include and not _match(rel, include)) or _match(rel, exclude) or size > limit:
                stats["skipped"] += 1
                continue
            wanted.append((rel, size, mtime))
        todo = []
        for rel, size, mtime in wanted:
            keep.add(rel)
            prev = state.get(rel)
            if prev and prev["size"] == size and prev["mtime"] == mtime and (dest / rel).is_file():
                stats["unchanged"] += 1
            else:
                todo.append((rel, size, mtime))
        deadline = time.time() + int(c.get("max_minutes", 30)) * 60
        opener = _ftp_session(c) if t == "ftp" else None
        try:
            for rel, size, mtime in todo:
                if time.time() > deadline:
                    stats["skipped"] += len(todo) - stats["downloaded"]
                    break
                target = (dest / rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".part")
                if t == "ftp":
                    with open(tmp, "wb") as fh:
                        opener.retrbinary("RETR " + c.get("path", "/").rstrip("/") + "/" + rel, fh.write)
                else:
                    _dav_get(c, rel, tmp, limit)
                os.replace(tmp, target)
                state[rel] = {"size": size, "mtime": mtime}
                stats["downloaded"] += 1
        finally:
            if opener:
                try:
                    opener.quit()
                except Exception:
                    pass
        if c.get("on_delete", "mirror") == "mirror":
            for rel in list(state):
                if rel not in keep:
                    (dest / rel).unlink(missing_ok=True)
                    state.pop(rel, None)
                    stats["removed"] += 1
        state_file.write_text(json.dumps(state), "utf-8")
        return stats

    # ---------------- reference sync (dry run or real) ----------------
    def sync(self, name: str, dry_run: bool = False) -> dict:
        c = self.cfgs.get(name)
        if not c:
            raise DmsError(404, "저장소를 찾을 수 없습니다")
        if c.get("mode", "reference") == "migrate":
            return self.migrate(name, dry_run)
        with self._lock:
            if name in self.running:
                raise DmsError(409, "이미 동기화 중입니다")
            self.running[name] = time.time()
        try:
            if dry_run:
                with self.app.lock:
                    st = self.app.ingest_stats(dry_run=True)
            else:
                st = self.app.ingest(mirror={name})
            res = {"dry_run": dry_run, **st.get("roots", {}).get(name + "/", {})}
            res["pruned_elsewhere"] = st.get("pruned", 0)
            if not dry_run:
                self._record(name, "sync", res, ok=not res.get("error"))
            return res
        finally:
            self.running.pop(name, None)

    # ---------------- migration ----------------
    def migrate(self, name: str, dry_run: bool = False) -> dict:
        c = self.cfgs[name]
        with self._lock:
            if name in self.running:
                raise DmsError(409, "이미 동기화 중입니다")
            self.running[name] = time.time()
        d, db = self.app.dms, self.app.store.db
        stats = {"dry_run": dry_run, "migrated": 0, "updated": 0, "unchanged": 0, "duplicate": 0, "error": 0, "too_large": 0, "blocked": 0, "excluded": 0, "source_deleted": 0, "errors": []}
        try:
            if c.get("type", "path") != "path":
                try:
                    self.mirror(c)
                except Exception as e:
                    raise DmsError(502, f"원격 저장소에 연결하지 못했습니다: {e}")
            root = self.local_path(c)
            if not root.is_dir():
                raise DmsError(502, f"경로에 접근할 수 없습니다: {root}")
            owner = self.app.users.get(c.get("owner", "admin"))
            if not owner:
                raise DmsError(400, f"이관 소유자 '{c.get('owner')}' 가 사용자 목록에 없습니다")
            include, exclude = c.get("include"), c.get("exclude", DEFAULT_EXCLUDE)
            limit = int(c.get("max_mb", 100)) * 1048576
            base_folder = c.get("folder") or f"이관/{name}"
            seen: set = set()
            files = sorted(p for p in root.rglob("*") if p.is_file() and ".edm_state.json" not in p.name and not p.name.endswith(".part"))
            for path in files:
                rel = path.relative_to(root).as_posix()
                seen.add(rel)
                try:
                    st = self._migrate_one(c, d, db, owner, root, path, rel, base_folder, include, exclude, limit, dry_run)
                except DmsError as e:
                    st = "error"
                    self._item(db, name, rel, "error", None, None, e.msg, dry_run)
                    stats["errors"].append(f"{rel}: {e.msg}")
                except Exception as e:                                          # one bad file never stops the run
                    st = "error"
                    self._item(db, name, rel, "error", None, None, type(e).__name__ + ": " + str(e)[:150], dry_run)
                    stats["errors"].append(f"{rel}: {type(e).__name__}")
                stats[st] = stats.get(st, 0) + 1
            for r in db.execute("SELECT rel, doc_id FROM source_items WHERE source=? AND status IN ('migrated','updated','unchanged')", (name,)).fetchall():
                if r[0] not in seen:
                    stats["source_deleted"] += 1
                    if not dry_run and c.get("on_delete", "flag") == "flag" and r[1]:
                        row = d.row(r[1])
                        if row and not row["props"].get("src_deleted"):
                            d.x("UPDATE dms_docs SET props=? WHERE id=?", (json.dumps({**row["props"], "src_deleted": "1"}, ensure_ascii=False), r[1]))
                    if not dry_run:
                        db.execute("UPDATE source_items SET status='source_deleted' WHERE source=? AND rel=?", (name, r[0]))
            stats["errors"] = stats["errors"][:50]
            if not dry_run:
                db.commit()
                self._record(name, "migrate", stats, ok=stats["error"] == 0)
            return stats
        finally:
            self.running.pop(name, None)

    def _item(self, db, name, rel, status, doc_id, sha, note, dry_run):
        if dry_run:
            return
        db.execute("INSERT INTO source_items(source,rel,status,doc_id,sha256,note,ts) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source,rel) DO UPDATE SET status=excluded.status, doc_id=excluded.doc_id, sha256=excluded.sha256, note=excluded.note, ts=excluded.ts",
                   (name, rel, status, doc_id, sha, note or "", now()))

    def _migrate_one(self, c, d, db, owner, root, path, rel, base_folder, include, exclude, limit, dry_run) -> str:
        name = c["name"]
        if (include and not _match(rel, include)) or _match(rel, exclude):
            return "excluded"
        if path.suffix.lower() in BLOCKED:
            self._item(db, name, rel, "blocked", None, None, "허용되지 않는 파일 형식", dry_run)
            return "blocked"
        size = path.stat().st_size
        if size > limit or size > d.max_upload:
            self._item(db, name, rel, "too_large", None, None, f"{size // 1048576}MB", dry_run)
            return "too_large"
        if size == 0:
            self._item(db, name, rel, "error", None, None, "빈 파일", dry_run)
            return "error"
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        prev = db.execute("SELECT sha256, doc_id, status FROM source_items WHERE source=? AND rel=?", (name, rel)).fetchone()
        if prev and prev[0] == sha and prev[2] in ("migrated", "updated", "unchanged") and d.row(prev[1]):
            self._item(db, name, rel, "unchanged", prev[1], sha, "", dry_run)
            return "unchanged"
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        props = {"이관원본": f"{name}:{rel}"[:190], "원본수정일": mtime[:10]}
        if prev and prev[1] and d.row(prev[1]) and not d.row(prev[1])["deleted_at"]:
            if dry_run:
                return "updated"
            d.new_version(owner, prev[1], path.name, data, f"{name} 동기화 ({mtime[:10]})")
            self._item(db, name, rel, "updated", prev[1], sha, "", dry_run)
            return "updated"
        dup = d.q1("SELECT doc_id FROM dms_versions WHERE sha256=? LIMIT 1", (sha,))
        if dup and c.get("duplicates", "skip") == "skip":
            self._item(db, name, rel, "duplicate", dup["doc_id"], sha, "동일 내용 문서가 이미 있습니다", dry_run)
            return "duplicate"
        if dry_run:
            return "migrated"
        parts = [_clean_segment(x) for x in rel.split("/")[:-1]]
        folder_path = "/".join([base_folder] + [x for x in parts if x])
        rule = next((r for r in c.get("acl_rules", []) if fnmatch.fnmatch(rel, r["glob"])), None)
        fid = d.ensure_folder_path(folder_path, c.get("folder_entries") if not parts else None)
        entries = rule.get("entries") if rule else None
        doc = d.register(owner, path.name, data, folder_id=fid, doc_type=c.get("doc_type", "GEN"), tags=["이관", name], props=props,
                         level=(rule or {}).get("level", c.get("level", 0)), entries=entries, comment=f"{name} 이관")
        self._item(db, name, rel, "migrated", doc["id"], sha, "", dry_run)
        return "migrated"

    def items(self, name: str, status: str | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT rel,status,doc_id,sha256,note,ts FROM source_items WHERE source=?" + (" AND status=?" if status else "") + " ORDER BY status<>'error', rel LIMIT ?"
        a = (name, status, limit) if status else (name, limit)
        return [{"rel": r[0], "status": r[1], "doc_id": r[2], "sha256": r[3], "note": r[4], "ts": r[5]} for r in self.app.store.db.execute(q, a).fetchall()]


# ======================= FTP =======================
def _ftp_session(c: dict):
    port = int(c.get("port", 21))
    if c.get("tls"):
        ftp = ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=60)
    else:
        ftp = ftplib.FTP(timeout=60)
    ftp.connect(c["host"], port)
    user = os.environ.get(c.get("user_env", ""), "") if c.get("user_env") else c.get("user", "anonymous")
    pw = os.environ.get(c.get("password_env", ""), "") if c.get("password_env") else c.get("password", "anonymous@")
    ftp.login(user, pw)
    if c.get("tls"):
        ftp.prot_p()
    return ftp


def _list_ftp(c: dict, max_depth: int = 20):
    ftp = _ftp_session(c)
    out: list = []
    base = c.get("path", "/").rstrip("/")

    def walk(rel: str, depth: int):
        if depth > max_depth or len(out) > int(c.get("max_files", 50000)):
            return
        path = base + ("/" + rel if rel else "") or "/"
        try:
            entries = list(ftp.mlsd(path, facts=["type", "size", "modify"]))
        except ftplib.error_perm:
            entries = []
            for n in ftp.nlst(path):
                n = n.rsplit("/", 1)[-1]
                try:
                    ftp.cwd(path + "/" + n)
                    entries.append((n, {"type": "dir"}))
                except ftplib.error_perm:
                    entries.append((n, {"type": "file", "size": str(ftp.size(path + "/" + n) or 0), "modify": ""}))
        for n, f in entries:
            if n in (".", "..") or f.get("type") in ("cdir", "pdir"):
                continue
            r = (rel + "/" if rel else "") + n
            if f.get("type") == "dir":
                walk(r, depth + 1)
            elif f.get("type") == "file":
                out.append((r, int(f.get("size", 0) or 0), f.get("modify", "")))
    try:
        walk("", 0)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return out


# ======================= WebDAV =======================
def _dav_request(c: dict, url: str, method: str, headers=None, body: bytes | None = None):
    req = urllib.request.Request(url, body, {"User-Agent": "icc-jeju-edm/1", **(headers or {})}, method=method)
    user = os.environ.get(c.get("user_env", ""), "") if c.get("user_env") else c.get("user", "")
    pw = os.environ.get(c.get("password_env", ""), "") if c.get("password_env") else c.get("password", "")
    if user:
        import base64
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    return urllib.request.urlopen(req, timeout=60)


def _dav_get(c: dict, rel: str, dest: Path, limit: int):
    url = c["url"].rstrip("/") + "/" + "/".join(urllib.parse.quote(s) for s in rel.split("/"))
    with _dav_request(c, url, "GET") as r, open(dest, "wb") as fh:
        n = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            n += len(chunk)
            if n > limit:
                raise SourceError("file larger than the limit")
            fh.write(chunk)


def _list_webdav(c: dict, max_depth: int = 20):
    base = c["url"].rstrip("/") + "/"
    base_path = urllib.parse.urlparse(base).path
    out: list = []

    def walk(url: str, depth: int):
        if depth > max_depth or len(out) > int(c.get("max_files", 50000)):
            return
        body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/><d:getlastmodified/><d:resourcetype/></d:prop></d:propfind>'
        with _dav_request(c, url, "PROPFIND", {"Depth": "1", "Content-Type": "application/xml"}, body) as r:
            raw = r.read(20 * 1024 * 1024)
        if re.search(rb"<!(DOCTYPE|ENTITY)", raw[:4096], re.I):
            raise SourceError("unsafe XML from server")
        root = ET.fromstring(raw)
        for resp in root.iter("{DAV:}response"):
            href = urllib.parse.unquote(urllib.parse.urlparse(resp.findtext("{DAV:}href") or "").path)
            if not href.startswith(base_path):
                continue
            rel = href[len(base_path):].strip("/")
            if not rel:
                continue
            is_dir = resp.find(".//{DAV:}resourcetype/{DAV:}collection") is not None
            if is_dir:
                walk(base + "/".join(urllib.parse.quote(s) for s in rel.split("/")) + "/", depth + 1)
            else:
                size = int(resp.findtext(".//{DAV:}getcontentlength") or 0)
                lm = resp.findtext(".//{DAV:}getlastmodified") or ""
                try:
                    lm = parsedate_to_datetime(lm).isoformat()
                except (TypeError, ValueError):
                    pass
                out.append((rel, size, lm))
    walk(base, 0)
    return out
