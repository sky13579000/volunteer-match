from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from .dms import add_days, now, today
from .errors import DmsError


class LifecycleMixin:
    """Retention, legal hold, trash, expiry and scheduled maintenance."""

    def _delete_blockers(self, row) -> str | None:
        if row["legal_hold"]:
            return "법적 보류(Legal Hold) 중인 문서는 삭제할 수 없습니다"
        if row["retention_until"] and row["retention_until"] > today():
            return f"보존기간({row['retention_until']})이 끝나기 전에는 삭제할 수 없습니다"
        if self._wf(row["id"]):
            return "결재가 진행 중인 문서는 삭제할 수 없습니다"
        return None

    def delete(self, p, doc_id) -> dict:
        """Soft delete into the trash. Retention and legal hold apply to EVERYONE, admins included."""
        row = self.get_row(p, doc_id, "delete")
        if row["origin"] == "crawled":
            raise DmsError(409, "원본 폴더에서 수집된 문서는 여기서 삭제할 수 없습니다 (원본을 지우면 다음 수집 때 제거됩니다)")
        why = self._delete_blockers(row)
        if why:
            raise DmsError(409, why)
        self.x("UPDATE dms_docs SET deleted_at=?, deleted_by=?, deleted_folder=folder_id, locked_by=NULL, locked_at=NULL WHERE id=?", (now(), p.id, doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)                                   # leaves the search index immediately
        self.log(p, row2, "deleted", "휴지통으로 이동")
        return {"deleted": row["doc_no"], "restorable_days": self.trash_days}

    def trash(self, p) -> list[dict]:
        out = []
        for r in self.q("SELECT id FROM dms_docs WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"):
            row = self.row(r["id"])
            if row["deleted_by"] == p.id or row["owner"] == p.id or self.admin(p):
                out.append({"id": row["id"], "doc_no": row["doc_no"], "title": row["title"], "deleted_at": row["deleted_at"],
                            "deleted_by": row["deleted_by"], "owner": row["owner"],
                            "purge_after": add_days(row["deleted_at"], self.trash_days)[:10]})
        return out

    def _trash_row(self, p, doc_id) -> dict:
        row = self.row(doc_id)
        if not row or not row["deleted_at"] or not (row["deleted_by"] == p.id or row["owner"] == p.id or self.admin(p)):
            raise DmsError(404, "휴지통에서 문서를 찾을 수 없습니다")
        return row

    def restore_doc(self, p, doc_id) -> dict:
        row = self._trash_row(p, doc_id)
        folder = row["deleted_folder"] if self.folder(row["deleted_folder"]) else 1
        self.x("UPDATE dms_docs SET deleted_at=NULL, deleted_by=NULL, folder_id=?, updated=? WHERE id=?", (folder, now(), doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)
        self.log(p, row2, "restored_from_trash")
        return self.get_or_summary(p, doc_id)

    def purge(self, p, doc_id) -> dict:
        """Permanent deletion: files, versions, comments, shares. Admin only, never during hold/retention."""
        row = self._trash_row(p, doc_id)
        if not self.admin(p):
            raise DmsError(403, "영구 삭제는 관리자만 할 수 있습니다")
        why = self._delete_blockers(row)
        if why:
            raise DmsError(409, why)
        self._purge_row(row, p.id)
        return {"purged": row["doc_no"]}

    def _purge_row(self, row, by: str):
        did = row["id"]
        with self.db:
            for t in ("dms_versions", "comments", "shares", "favorites", "recent", "notifications"):
                self.db.execute(f"DELETE FROM {t} WHERE doc_id=?", (did,))
            for w in self.db.execute("SELECT id FROM workflows WHERE doc_id=?", (did,)).fetchall():
                self.db.execute("DELETE FROM wf_steps WHERE wf_id=?", (w[0],))
            self.db.execute("DELETE FROM workflows WHERE doc_id=?", (did,))
            self.db.execute("DELETE FROM dms_docs WHERE id=?", (did,))
        shutil.rmtree(self.repo / str(did), ignore_errors=True)
        self.store.forget(row["source"])
        self.store.log_audit(by, f"[영구 삭제] {row['doc_no']}", [row["source"]], False, "dms")

    def set_hold(self, p, doc_id, hold: bool, reason="") -> dict:
        if not self.admin(p):
            raise DmsError(403, "법적 보류는 관리자만 설정할 수 있습니다")
        row = self.row(doc_id)
        if not row:
            raise DmsError(404, "문서를 찾을 수 없습니다")
        self.x("UPDATE dms_docs SET legal_hold=?, updated=? WHERE id=?", (int(bool(hold)), now(), doc_id))
        self.log(p, self.row(doc_id), "legal_hold_on" if hold else "legal_hold_off", str(reason)[:200])
        return self.get_or_summary(p, doc_id)

    def maintenance(self, p=None) -> dict:
        """Scheduled job (also runnable from the admin console): expiry, expiry warnings, trash purge."""
        if p is not None and not self.admin(p):
            raise DmsError(403, "관리자만 실행할 수 있습니다")
        res = {"expired": 0, "warned": 0, "purged": 0, "retention_ended": 0}
        t0, warn_until = today(), (datetime.now(timezone.utc) + timedelta(days=self.warn_days)).date().isoformat()
        with self.batch():
            for r in self.q("SELECT id FROM dms_docs WHERE deleted_at IS NULL AND origin='managed' AND expires_at IS NOT NULL"):
                row = self.row(r["id"])
                if row["legal_hold"]:
                    continue
                if row["expires_at"] <= t0 and row["status"] != "expired":
                    self.x("UPDATE dms_docs SET status='expired', updated=? WHERE id=?", (now(), row["id"]))
                    row2 = self.row(row["id"])
                    self.reindex(row2)                       # drops out of everyone's search / AI answers
                    self.notify(row["owner"], "expired", row["id"], f"만료됨: {row['title']} ({row['doc_no']}) — 검색·AI 답변에서 제외되었습니다")
                    self.log("system", row2, "expired")
                    res["expired"] += 1
                elif row["expires_at"] <= warn_until and row["expires_at"] > t0 and not row["expire_notified"]:
                    self.x("UPDATE dms_docs SET expire_notified=1 WHERE id=?", (row["id"],))
                    self.notify(row["owner"], "expiring", row["id"], f"만료 예정({row['expires_at']}): {row['title']} ({row['doc_no']})")
                    res["warned"] += 1
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.trash_days)).isoformat(timespec="seconds")
            for r in self.q("SELECT id FROM dms_docs WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,)):
                row = self.row(r["id"])
                if not self._delete_blockers(row):
                    self._purge_row(row, "system")
                    res["purged"] += 1
        return res

    def extend_expiry(self, p, doc_id, new_date) -> dict:
        row = self.get_row(p, doc_id, "edit")
        d = self.clean_date(new_date, "만료일")
        status = "approved" if row["status"] == "expired" and row["effective_ver"] else row["status"]
        self.x("UPDATE dms_docs SET expires_at=?, expire_notified=0, status=?, updated=? WHERE id=?", (d, status, now(), doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)
        self.log(p, row2, "expiry_changed", str(d))
        return self.get_or_summary(p, doc_id)
