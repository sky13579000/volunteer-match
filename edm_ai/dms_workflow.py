from __future__ import annotations

import json

from .dms import now
from .errors import DmsError


class WorkflowMixin:
    """Approval (결재). Sequential: one active step at a time. Parallel: all steps active, all must approve.
    Any rejection rejects the version. The requester can never approve their own request."""

    def notify(self, user_id: str, kind: str, doc_id, text: str, exclude: str | None = None):
        if user_id and user_id != exclude and user_id in self.app.users:
            self.x("INSERT INTO notifications(user_id,kind,doc_id,text,ts) VALUES(?,?,?,?,?)", (user_id, kind, doc_id, text[:300], now()))

    def _wf(self, doc_id, status="pending"):
        w = self.q1("SELECT * FROM workflows WHERE doc_id=? AND status=? ORDER BY id DESC", (doc_id, status))
        if w:
            w["steps"] = self.q("SELECT * FROM wf_steps WHERE wf_id=? ORDER BY ord, id", (w["id"],))
        return w

    def _active_steps(self, w) -> list[dict]:
        pend = [s for s in w["steps"] if s["status"] == "pending"]
        if not pend:
            return []
        if w["mode"] == "parallel":
            return pend
        first = min(s["ord"] for s in pend)
        return [s for s in pend if s["ord"] == first]

    def workflow_state(self, p, row) -> dict | None:
        w = self._wf(row["id"]) or self.q1("SELECT id FROM workflows WHERE doc_id=? ORDER BY id DESC", (row["id"],))
        if not w:
            return None
        w = self._wf(row["id"], None) if False else self.q1("SELECT * FROM workflows WHERE id=?", (w["id"],))
        w["steps"] = self.q("SELECT ord,approver,status,comment,acted_at,delegated_from,id FROM wf_steps WHERE wf_id=? ORDER BY ord, id", (w["id"],))
        active = {s["id"] for s in self._active_steps(w)} if w["status"] == "pending" else set()
        mine = [s for s in w["steps"] if s["id"] in active and s["approver"] == p.id]
        for s in w["steps"]:
            s["active"] = s["id"] in active
            s.pop("id")
        return {"id": w["id"], "ver": w["ver"], "status": w["status"], "mode": w["mode"], "requester": w["requester"],
                "note": w["note"], "created": w["created"], "completed": w["completed"], "steps": w["steps"],
                "can_act": bool(mine), "can_cancel": w["status"] == "pending" and (w["requester"] == p.id or self.admin(p))}

    def submit(self, p, doc_id, approvers: list[str] | None = None, mode="sequential", note="") -> dict:
        row = self.get_row(p, doc_id, "edit")
        if row["origin"] != "managed":
            raise DmsError(409, "수집된 문서는 결재할 수 없습니다")
        t = self.doc_type(row["doc_type"])
        if self._wf(doc_id):
            raise DmsError(409, "이미 결재가 진행 중입니다")
        ver = self._ver(row, row["current_ver"])
        if ver["status"] not in ("draft", "rejected"):
            raise DmsError(409, "결재를 올릴 수 있는 상태가 아닙니다 (작성 중/반려 상태만 가능)")
        if mode not in ("sequential", "parallel"):
            raise DmsError(400, "결재 방식은 sequential/parallel 입니다")
        approvers = [str(a) for a in (approvers or t["default_approvers"])]
        if not approvers:
            raise DmsError(400, "결재자를 한 명 이상 지정하세요")
        if len(approvers) > 10 or len(set(approvers)) != len(approvers):
            raise DmsError(400, "결재자는 중복 없이 최대 10명입니다")
        for a in approvers:
            if a not in self.app.users:
                raise DmsError(400, f"알 수 없는 결재자입니다: {a}")
            if a == p.id:
                raise DmsError(400, "본인을 결재자로 지정할 수 없습니다")
            if a == row["owner"]:
                raise DmsError(400, "문서 소유자는 결재자가 될 수 없습니다")
        ts = now()
        cur = self.x("INSERT INTO workflows(doc_id,ver,requester,mode,status,note,created) VALUES(?,?,?,?,'pending',?,?)",
                     (doc_id, ver["ver"], p.id, mode, str(note)[:500], ts))
        with self.db:
            for i, a in enumerate(approvers):
                self.db.execute("INSERT INTO wf_steps(wf_id,ord,approver) VALUES(?,?,?)", (cur.lastrowid, i, a))
            self.db.execute("UPDATE dms_versions SET status='in_review' WHERE doc_id=? AND ver=?", (doc_id, ver["ver"]))
            self.db.execute("UPDATE dms_docs SET status='in_review', updated=? WHERE id=?", (ts, doc_id))
        row = self.row(doc_id)
        self.reindex(row)                                   # approvers gain read access to the draft
        self.log(p, row, "submitted", f"{mode}: {', '.join(approvers)}")
        for s in self._active_steps(self._wf(doc_id)):
            self.notify(s["approver"], "approval_request", doc_id, f"결재 요청: {row['title']} ({row['doc_no']}) — 요청자 {p.id}")
        return self.get(p, doc_id)

    def _my_step(self, p, doc_id):
        w = self._wf(doc_id)
        if not w:
            raise DmsError(409, "진행 중인 결재가 없습니다")
        mine = [s for s in self._active_steps(w) if s["approver"] == p.id]
        if not mine:
            raise DmsError(403, "현재 본인이 처리할 결재 단계가 아닙니다")
        return w, mine[0]

    def approve(self, p, doc_id, comment="") -> dict:
        row = self.row(doc_id)
        if not row or row["deleted_at"]:
            raise DmsError(404, "문서를 찾을 수 없습니다")
        if p.id not in self.approvers_of(doc_id):
            raise DmsError(404, "문서를 찾을 수 없습니다")
        w, step = self._my_step(p, doc_id)
        if w["requester"] == p.id:
            raise DmsError(403, "본인이 요청한 결재는 승인할 수 없습니다")
        self.x("UPDATE wf_steps SET status='approved', comment=?, acted_at=? WHERE id=?", (str(comment)[:500], now(), step["id"]))
        w = self._wf(doc_id)
        if not any(s["status"] == "pending" for s in w["steps"]):
            self._finish(w, "approved", p)
        else:
            for s in self._active_steps(w):
                if s["ord"] != step["ord"] or w["mode"] == "sequential":
                    self.notify(s["approver"], "approval_request", doc_id, f"결재 요청: {row['title']} ({row['doc_no']}) — 앞 단계 승인 완료")
        self.log(p, self.row(doc_id), "approved_step", str(comment)[:200])
        return self.get_or_summary(p, doc_id)

    def reject(self, p, doc_id, comment="") -> dict:
        row = self.row(doc_id)
        if not row or row["deleted_at"] or p.id not in self.approvers_of(doc_id):
            raise DmsError(404, "문서를 찾을 수 없습니다")
        if not str(comment).strip():
            raise DmsError(400, "반려 사유를 입력하세요")
        w, step = self._my_step(p, doc_id)
        self.x("UPDATE wf_steps SET status='rejected', comment=?, acted_at=? WHERE id=?", (str(comment)[:500], now(), step["id"]))
        self.x("UPDATE wf_steps SET status='skipped' WHERE wf_id=? AND status='pending'", (w["id"],))
        self._finish(self._wf(w["doc_id"]) or w, "rejected", p, comment)
        self.log(p, self.row(doc_id), "rejected", str(comment)[:200])
        return self.get_or_summary(p, doc_id)

    def _finish(self, w, outcome: str, actor, note=""):
        ts = now()
        doc_id = w["doc_id"]
        row = self.row(doc_id)
        t = self.doc_type(row["doc_type"])
        self.x("UPDATE workflows SET status=?, completed=? WHERE id=?", (outcome, ts, w["id"]))
        if outcome == "approved":
            self._approve_version(doc_id, w["ver"], t, actor.id)
        else:
            self.x("UPDATE dms_versions SET status='rejected' WHERE doc_id=? AND ver=?", (doc_id, w["ver"]))
            self.x("UPDATE dms_docs SET status=CASE WHEN effective_ver IS NULL THEN 'rejected' ELSE 'approved' END, updated=? WHERE id=?", (ts, doc_id))
        row = self.row(doc_id)
        self.reindex(row)
        label = "승인" if outcome == "approved" else "반려"
        self.notify(w["requester"], outcome, doc_id, f"{label}: {row['title']} ({row['doc_no']})" + (f" — {note}" if note else ""), exclude=actor.id)
        if row["owner"] != w["requester"]:
            self.notify(row["owner"], outcome, doc_id, f"{label}: {row['title']} ({row['doc_no']})", exclude=actor.id)

    def delegate(self, p, doc_id, to: str, comment="") -> dict:
        row = self.row(doc_id)
        if not row or row["deleted_at"] or p.id not in self.approvers_of(doc_id):
            raise DmsError(404, "문서를 찾을 수 없습니다")
        w, step = self._my_step(p, doc_id)
        if to not in self.app.users or to in (p.id, w["requester"], row["owner"]):
            raise DmsError(400, "위임할 수 없는 사용자입니다")
        if any(s["approver"] == to and s["status"] == "pending" for s in w["steps"]):
            raise DmsError(400, "이미 결재 단계에 있는 사용자입니다")
        self.x("UPDATE wf_steps SET approver=?, delegated_from=? WHERE id=?", (to, p.id, step["id"]))
        self.reindex_doc(doc_id)
        self.notify(to, "approval_request", doc_id, f"결재 위임: {row['title']} ({row['doc_no']}) — {p.id} 님이 위임")
        self.log(p, self.row(doc_id), "delegated", f"-> {to} {str(comment)[:100]}")
        return {"delegated_to": to}

    def cancel_workflow(self, p, doc_id) -> dict:
        row = self.get_row(p, doc_id, "read")
        w = self._wf(doc_id)
        if not w:
            raise DmsError(409, "진행 중인 결재가 없습니다")
        if w["requester"] != p.id and not self.admin(p):
            raise DmsError(403, "요청자 또는 관리자만 회수할 수 있습니다")
        ts = now()
        with self.db:
            self.db.execute("UPDATE workflows SET status='cancelled', completed=? WHERE id=?", (ts, w["id"]))
            self.db.execute("UPDATE wf_steps SET status='skipped' WHERE wf_id=? AND status='pending'", (w["id"],))
            self.db.execute("UPDATE dms_versions SET status='draft' WHERE doc_id=? AND ver=?", (doc_id, w["ver"]))
            self.db.execute("UPDATE dms_docs SET status=CASE WHEN effective_ver IS NULL THEN 'draft' ELSE 'approved' END, updated=? WHERE id=?", (ts, doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)
        for s in w["steps"]:
            self.notify(s["approver"], "cancelled", doc_id, f"결재 회수: {row['title']} ({row['doc_no']})", exclude=p.id)
        self.log(p, row2, "workflow_cancelled")
        return self.get_or_summary(p, doc_id)

    def my_tasks(self, p) -> list[dict]:
        out = []
        for w in self.q("SELECT * FROM workflows WHERE status='pending'"):
            w["steps"] = self.q("SELECT * FROM wf_steps WHERE wf_id=? ORDER BY ord, id", (w["id"],))
            if any(s["approver"] == p.id for s in self._active_steps(w)):
                row = self.row(w["doc_id"])
                if row and not row["deleted_at"]:
                    out.append({"doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"], "requester": w["requester"],
                                "ver": w["ver"], "mode": w["mode"], "note": w["note"], "since": w["created"]})
        return sorted(out, key=lambda x: x["since"])

    def my_requests(self, p) -> list[dict]:
        out = []
        for w in self.q("SELECT * FROM workflows WHERE requester=? ORDER BY id DESC LIMIT 50", (p.id,)):
            row = self.row(w["doc_id"])
            if row and not row["deleted_at"]:
                out.append({"doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"], "status": w["status"], "ver": w["ver"], "created": w["created"]})
        return out
