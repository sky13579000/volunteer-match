from __future__ import annotations

import csv
import hashlib
import io
import re
import struct

from .dms import now
from .errors import DmsError

ZIP_EXT = {".docx", ".xlsx", ".pptx", ".hwpx", ".zip", ".odt", ".ods", ".odp"}
TAG_RE = re.compile(rb"EDM-ID:([A-Za-z0-9._-]{1,40}):v(\d{1,5})")
SHA_RE = re.compile(r"[0-9a-fA-F]{64}")


def tag_bytes(name: str, data: bytes, doc_no: str, ver: int) -> bytes:
    """Mark a delivered copy with its document number and version without touching the content: the ZIP archive comment for Office/HWPX files,
    a trailing comment after %%EOF for PDF. A copy re-saved by an application usually loses the mark (the hash lookup is the primary check)."""
    tag = f"EDM-ID:{doc_no}:v{int(ver)}".encode()
    n = name.lower()
    if any(n.endswith(e) for e in ZIP_EXT) and data[:2] == b"PK":
        i = data.rfind(b"PK\x05\x06")
        if i < 0 or len(data) - i < 22:
            return data
        clen = struct.unpack("<H", data[i + 20:i + 22])[0]
        if i + 22 + clen != len(data):
            return data                                                           # unexpected layout (e.g. appended data): leave it alone
        return data[:i + 20] + struct.pack("<H", len(tag)) + tag
    if n.endswith(".pdf") and data[:5] == b"%PDF-":
        return data + b"\n%" + tag + b"\n"
    return data


def read_tag(data: bytes) -> tuple[str, int] | None:
    tail = data[-70000:]
    m = None
    for m in TAG_RE.finditer(tail):
        pass
    return (m.group(1).decode(), int(m.group(2))) if m else None


class TraceMixin:
    """Latest-version tracing ("이 파일이 최신본입니까?"), delivered-copy receipts and per-document activity."""

    # =============== activity ===============
    def _trace_cfg(self) -> dict:
        return {"log_views": True, "tag_downloads": False, **(self.app.cfg.get("tracing") or {})}

    def track(self, p, row, action: str, ver=None, out_sha: str | None = None):
        cfg = self._trace_cfg()
        uid = p if isinstance(p, str) else p.id
        if action == "view":
            if not cfg["log_views"]:
                return
            if self.q1("SELECT 1 AS x FROM doc_activity WHERE doc_id=? AND user_id=? AND action='view' AND ts>?", (row["id"], uid, _ago(600))):
                return                                                              # one entry per user per 10 minutes
        self.x("INSERT INTO doc_activity(doc_id,user_id,action,ver,out_sha,ts) VALUES(?,?,?,?,?,?)", (row["id"], uid, action, ver, out_sha, now()))

    def doc_activity(self, p, doc_id) -> dict:
        row = self.get_row(p, doc_id, "edit")
        rows = self.q("SELECT user_id,action,ver,ts FROM doc_activity WHERE doc_id=? ORDER BY id DESC LIMIT 500", (row["id"],))
        per: dict[str, dict] = {}
        for r in rows:
            u = per.setdefault(r["user_id"], {"user": r["user_id"], "views": 0, "downloads": 0, "last": r["ts"]})
            u["views" if r["action"] == "view" else "downloads"] += 1
        names = self.app.users
        return {"doc_no": row["doc_no"], "users": sorted(per.values(), key=lambda x: x["last"], reverse=True), "recent": rows[:100],
                "totals": {"views": sum(1 for r in rows if r["action"] == "view"), "downloads": sum(1 for r in rows if r["action"] == "download")},
                "names": {k: names[k].name for k in per if k in names}}

    def activity_csv(self, p, doc_id) -> str:
        row = self.get_row(p, doc_id, "edit")
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["일시", "사용자", "구분", "버전"])
        for r in self.q("SELECT user_id,action,ver,ts FROM doc_activity WHERE doc_id=? ORDER BY id DESC LIMIT 5000", (row["id"],)):
            w.writerow([r["ts"], r["user_id"], {"view": "열람", "download": "다운로드"}.get(r["action"], r["action"]), r["ver"] or ""])
        return "﻿" + out.getvalue()

    # =============== is this the latest? ===============
    def verify_copy(self, p, data: bytes | None = None, sha: str | None = None, filename: str = "") -> dict:
        """Look a file (or just its SHA-256) up among all versions the caller may read, and among the copies the platform delivered."""
        if data:
            self.check_size(data)
            sha = hashlib.sha256(data).hexdigest()
        sha = str(sha or "").strip().lower()
        if not SHA_RE.fullmatch(sha):
            raise DmsError(400, "파일 또는 64자리 SHA-256 값을 입력하세요")
        tag = read_tag(data) if data else None
        found: dict[int, dict] = {}

        def add(doc_id, ver, via, exact):
            row = self.row(doc_id)
            if not row or row["deleted_at"] or not self.can_read(p, row) or doc_id in found and found[doc_id]["exact"] and not exact:
                return
            latest = row["effective_ver"] or row["current_ver"]
            vis = self.visible_vers(p, row)
            found[doc_id] = {"doc_id": doc_id, "doc_no": row["doc_no"], "title": row["title"], "ver": ver, "latest_ver": latest if latest in vis else row["effective_ver"], "via": via, "exact": exact,
                             "is_latest": exact and ver == latest, "status": row["status"], "expired": row["status"] == "expired"}
        for v in self.q("SELECT doc_id,ver FROM dms_versions WHERE sha256=?", (sha,)):
            add(v["doc_id"], v["ver"], "내용 일치 (해시)", True)
        for r in self.q("SELECT doc_id,ver FROM doc_activity WHERE out_sha=? AND action='download'", (sha,)):
            add(r["doc_id"], r["ver"], "플랫폼에서 내려받은 사본 (문서번호 표식 포함)", True)
        if tag:
            r = self.q1("SELECT id FROM dms_docs WHERE doc_no=?", (tag[0],))
            if r and r["id"] not in found:
                add(r["id"], tag[1], "문서번호 표식 (내용은 수정됨)", False)
        res = list(found.values())
        for x in res:
            x.pop("exact", None)
        state = "latest" if any(x["is_latest"] for x in res) else "outdated" if any(x["ver"] != x["latest_ver"] and x["via"] != "문서번호 표식 (내용은 수정됨)" for x in res) else "modified" if res else "unknown"
        msg = {"latest": "현재 최신본과 동일한 파일입니다.", "outdated": "이전 버전의 파일입니다. 최신본을 받아 사용하세요.", "modified": "문서번호 표식은 있으나 내용이 플랫폼의 어떤 버전과도 다릅니다 (수정된 사본).",
               "unknown": "이 플랫폼에서 찾을 수 없는 파일이거나 열람 권한이 없는 문서입니다."}[state]
        self.store.log_audit(p.id, f"[사본 확인] {sha[:12]} → {state}", [], False, "dms")
        return {"sha256": sha, "state": state, "message": msg, "matches": res, "filename": filename[:200]}


def _ago(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
