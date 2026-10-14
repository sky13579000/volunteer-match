from __future__ import annotations

import json

from .dms import now
from .errors import DmsError

ROLES = {"viewer": "열람", "editor": "편집"}
MAX_PENDING = 20


class AccessMixin:
    """Access requests (권한 신청) and document subscriptions (구독).

    A request never reveals whether a document exists: asking for something that is missing, hidden, or already
    yours all produce a neutral answer, and the requester only learns the title after a manager approves."""

    GENERIC = {"ok": True, "message": "신청이 접수되었습니다. 담당자가 승인하면 알림을 받습니다."}

    # ---------------- requests ----------------
    def request_access(self, p, ref, role="viewer", reason="") -> dict:
        role = str(role)
        if role not in ROLES:
            raise DmsError(400, "신청 권한은 viewer 또는 editor 입니다")
        ref = str(ref or "").strip()[:60]
        if not ref:
            raise DmsError(400, "문서번호를 입력하세요")
        if self.q1("SELECT COUNT(*) AS n FROM access_requests WHERE requester=? AND status='pending'", (p.id,))["n"] >= MAX_PENDING:
            raise DmsError(429, f"처리 중인 신청이 너무 많습니다 (최대 {MAX_PENDING}건)")
        row = self.row(int(ref)) if ref.isdigit() else None
        if row is None:
            hit = self.q1("SELECT id FROM dms_docs WHERE doc_no=?", (ref,))
            row = self.row(hit["id"]) if hit else None
        if row is None or row["deleted_at"] or row["origin"] != "managed":
            return dict(self.GENERIC)                                    # nothing to ask for - same answer as a real request
        pr = self.perms(p, row)
        if ("edit" in pr) or (role == "viewer" and "read" in pr):
            return {"ok": True, "message": "이미 해당 권한으로 접근할 수 있는 문서입니다.", "doc_id": row["id"]}
        if self.q1("SELECT 1 AS x FROM access_requests WHERE doc_id=? AND requester=? AND role=? AND status='pending'", (row["id"], p.id, role)):
            return dict(self.GENERIC)
        self.x("INSERT INTO access_requests(doc_id,requester,role,reason,created,ref) VALUES(?,?,?,?,?,?)",
               (row["id"], p.id, role, str(reason or "").strip()[:500], now(), ref))
        for uid in self._managers(row):
            self.notify(uid, "access_request", row["id"], f"{p.name} 님이 {row['doc_no']} 문서의 {ROLES[role]} 권한을 신청했습니다", exclude=p.id)
        self.log(p, row, "access_requested", role)
        return dict(self.GENERIC)

    def _managers(self, row) -> list[str]:
        ids = {row["owner"]} | {e["id"] for e in row["entries"] if e["type"] == "user" and e["role"] == "manager"}
        return sorted(u for u in ids if u in self.app.users)

    def list_access_requests(self, p) -> dict:
        mine, decide = [], []
        for r in self.q("SELECT * FROM access_requests WHERE requester=? ORDER BY id DESC LIMIT 50", (p.id,)):
            ok = r["status"] == "approved"
            row = self.row(r["doc_id"]) if ok else None
            mine.append({"id": r["id"], "ref": r["ref"], "role": r["role"], "status": r["status"], "reason": r["reason"], "note": r["note"], "created": r["created"],
                         "doc_id": r["doc_id"] if ok else None, "title": row["title"] if row else None})
        for r in self.q("SELECT * FROM access_requests WHERE status='pending' ORDER BY id"):
            row = self.row(r["doc_id"])
            if row and not row["deleted_at"] and "acl" in self.perms(p, row) and self.can_read(p, row) or (row and self.admin(p)):
                u = self.app.users.get(r["requester"])
                decide.append({"id": r["id"], "doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"] if self.can_read(p, row) else None,
                               "requester": r["requester"], "requester_name": u.name if u else r["requester"], "role": r["role"], "reason": r["reason"], "created": r["created"]})
        return {"mine": mine, "to_decide": decide}

    def decide_access(self, p, req_id, approve: bool, note="") -> dict:
        r = self.q1("SELECT * FROM access_requests WHERE id=?", (int(req_id),))
        if not r or r["status"] != "pending":
            raise DmsError(404, "신청을 찾을 수 없습니다")
        row = self.get_row(p, r["doc_id"], "acl")                       # needs permission-management rights on this document
        note = str(note or "").strip()[:300]
        if approve:
            entries = [dict(e) for e in row["entries"]]
            if any(e["type"] == "deny" and e["id"] == r["requester"] for e in entries):
                raise DmsError(409, "이 사용자는 문서에서 제외(거부)로 지정되어 있습니다. 먼저 제외 항목을 해제하세요")
            rank = {"viewer": 1, "editor": 2, "manager": 3}
            cur = next((e for e in entries if e["type"] == "user" and e["id"] == r["requester"]), None)
            if cur:
                cur["role"] = max(cur["role"], r["role"], key=lambda x: rank[x])
            else:
                entries.append({"type": "user", "id": r["requester"], "role": r["role"]})
            self.set_acl(p, row["id"], entries=entries)
        self.x("UPDATE access_requests SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?", ("approved" if approve else "denied", p.id, now(), note, r["id"]))
        self.notify(r["requester"], "access_granted" if approve else "access_denied", row["id"] if approve else None,
                    (f"{row['doc_no']} 문서의 {ROLES[r['role']]} 권한이 승인되었습니다" if approve else f"{r['ref']} 문서 접근 신청이 거절되었습니다" + (f": {note}" if note else "")), exclude=p.id)
        self.log(p, row, "access_granted" if approve else "access_denied", f"{r['requester']} {r['role']}")
        return {"id": r["id"], "status": "approved" if approve else "denied"}

    # ---------------- follow ----------------
    def set_follow(self, p, doc_id, on: bool) -> dict:
        row = self.get_row(p, doc_id, "read")
        if on:
            self.x("INSERT OR IGNORE INTO follows(user_id,doc_id,ts) VALUES(?,?,?)", (p.id, row["id"], now()))
        else:
            self.x("DELETE FROM follows WHERE user_id=? AND doc_id=?", (p.id, row["id"]))
        return {"following": bool(on)}

    def is_following(self, p, doc_id) -> bool:
        return bool(self.q1("SELECT 1 AS x FROM follows WHERE user_id=? AND doc_id=?", (p.id, doc_id)))

    def notify_followers(self, row, text: str, actor: str):
        """Followers who can STILL read the document are told; a follower who lost access hears nothing about it."""
        for f in self.q("SELECT user_id FROM follows WHERE doc_id=?", (row["id"],)):
            u = self.app.users.get(f["user_id"])
            if u and f["user_id"] != actor and self.can_read(u, row):
                self.notify(f["user_id"], "follow_update", row["id"], f"{row['doc_no']} {text}")

    def my_follows(self, p) -> list[dict]:
        out = []
        for f in self.q("SELECT doc_id FROM follows WHERE user_id=? ORDER BY ts DESC", (p.id,)):
            row = self.row(f["doc_id"])
            if row and not row["deleted_at"] and self.can_read(p, row):
                out.append(self.summary(p, row, False))
        return out

    # ---------------- activity feed ----------------
    def activity(self, p, limit=40) -> list[dict]:
        """Recent events on documents the caller can read (never events of hidden documents)."""
        out, seen = [], {}
        for h in self.q("SELECT source,ts,user_id,action,detail FROM doc_history ORDER BY id DESC LIMIT 600"):
            ok = seen.get(h["source"])
            if ok is None:
                hit = self.q1("SELECT id FROM dms_docs WHERE source=?", (h["source"],))
                row = self.row(hit["id"]) if hit else None
                ok = seen[h["source"]] = row if row and not row["deleted_at"] and self.can_read(p, row) else False
            if not ok or h["action"] in ("downloaded", "share_viewed", "share_downloaded"):
                continue
            u = self.app.users.get(h["user_id"])
            detail = h["detail"] if h["action"] in ("registered", "new_version", "commented", "moved") else ""
            out.append({"ts": h["ts"], "user": h["user_id"], "user_name": u.name if u else h["user_id"], "action": h["action"], "detail": detail[:120],
                        "doc_id": ok["id"], "doc_no": ok["doc_no"], "title": ok["title"]})
            if len(out) >= limit:
                break
        return out
