from __future__ import annotations

import json
import re

from .dms import now
from .errors import DmsError

_MENTION = re.compile(r"@([A-Za-z0-9_.\-]{1,40})")


class CollabMixin:
    """Comments with @mentions, notification inbox, favourites, recent items, saved searches."""

    def add_comment(self, p, doc_id, text, parent_id=None) -> dict:
        row = self.get_row(p, doc_id, "comment")
        text = str(text or "").strip()
        if not text or len(text) > 2000:
            raise DmsError(400, "댓글은 1~2000자입니다")
        if parent_id is not None:
            par = self.q1("SELECT id FROM comments WHERE id=? AND doc_id=? AND deleted=0", (parent_id, doc_id))
            if not par:
                raise DmsError(404, "원 댓글을 찾을 수 없습니다")
        cur = self.x("INSERT INTO comments(doc_id,user_id,text,ts,parent_id) VALUES(?,?,?,?,?)", (doc_id, p.id, text, now(), parent_id))
        notified = set()
        for uid in _MENTION.findall(text):
            u = self.app.users.get(uid)
            # a mention never reveals a document: the person is notified only if they can already read it
            if u and uid != p.id and self.can_read(u, row):
                self.notify(uid, "mention", doc_id, f"{p.id} 님이 언급: {text[:120]}")
                notified.add(uid)
        for uid in {row["owner"]} - notified:
            self.notify(uid, "comment", doc_id, f"{p.id}: {text[:120]}", exclude=p.id)
        self.log(p, row, "commented")
        return self.q1("SELECT * FROM comments WHERE id=?", (cur.lastrowid,))

    def comments(self, p, doc_id) -> list[dict]:
        self.get_row(p, doc_id, "read")
        return [{"id": c["id"], "user": c["user_id"], "text": c["text"], "ts": c["ts"], "parent_id": c["parent_id"],
                 "can_delete": c["user_id"] == p.id or self.admin(p)}
                for c in self.q("SELECT * FROM comments WHERE doc_id=? AND deleted=0 ORDER BY id", (doc_id,))]

    def delete_comment(self, p, doc_id, cid) -> dict:
        row = self.get_row(p, doc_id, "read")
        c = self.q1("SELECT * FROM comments WHERE id=? AND doc_id=? AND deleted=0", (cid, doc_id))
        if not c:
            raise DmsError(404, "댓글을 찾을 수 없습니다")
        if c["user_id"] != p.id and not self.admin(p) and "acl" not in self.perms(p, row):
            raise DmsError(403, "본인 댓글만 삭제할 수 있습니다")
        self.x("UPDATE comments SET deleted=1 WHERE id=?", (cid,))
        self.log(p, row, "comment_deleted", str(cid))
        return {"deleted": cid}

    # ---- notifications ----
    def notifications(self, p, unread_only=False, limit=50) -> dict:
        rows = self.q("SELECT id,kind,doc_id,text,ts,read FROM notifications WHERE user_id=?" + (" AND read=0" if unread_only else "") +
                      " ORDER BY id DESC LIMIT ?", (p.id, min(int(limit), 200)))
        # a notification may outlive the reader's access to the document: hide the link, keep the text out
        for r in rows:
            row = self.row(r["doc_id"]) if r["doc_id"] else None
            if row and (row["deleted_at"] or not (self.can_read(p, row) or p.id in self.approvers_of(row["id"]) or row["owner"] == p.id)):
                r["doc_id"], r["text"] = None, "(접근 권한이 없는 문서에 대한 알림)"
        n = self.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0", (p.id,))["n"]
        return {"unread": n, "items": rows}

    def mark_read(self, p, ids=None) -> dict:
        if ids is None:
            self.x("UPDATE notifications SET read=1 WHERE user_id=?", (p.id,))
        else:
            with self.db:
                for i in ids[:500]:
                    self.db.execute("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", (int(i), p.id))
        return {"unread": self.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0", (p.id,))["n"]}

    # ---- favourites / recent ----
    def set_favorite(self, p, doc_id, on: bool) -> dict:
        self.get_row(p, doc_id, "read")
        if on:
            self.x("INSERT OR REPLACE INTO favorites VALUES(?,?,?)", (p.id, doc_id, now()))
        else:
            self.x("DELETE FROM favorites WHERE user_id=? AND doc_id=?", (p.id, doc_id))
        return {"favorite": bool(on)}

    def _docs_from(self, p, ids: list[int], with_perms=False) -> list[dict]:
        out = []
        for i in ids:
            row = self.row(i)
            if row and not row["deleted_at"] and self.can_read(p, row):      # a stale favourite never reveals a hidden document
                out.append(self.summary(p, row, with_perms))
        return out

    def favorites(self, p) -> list[dict]:
        return self._docs_from(p, [r["doc_id"] for r in self.q("SELECT doc_id FROM favorites WHERE user_id=? ORDER BY ts DESC", (p.id,))])

    def recent(self, p, limit=10) -> list[dict]:
        return self._docs_from(p, [r["doc_id"] for r in self.q("SELECT doc_id FROM recent WHERE user_id=? ORDER BY ts DESC LIMIT ?", (p.id, limit * 3))])[:limit]

    # ---- related documents ----
    def _resolve(self, p, other) -> dict:
        """Accept a document id or a document number. Unreadable == nonexistent."""
        s = str(other or "").strip()
        row = self.row(int(s)) if s.isdigit() else (self.q1("SELECT id FROM dms_docs WHERE doc_no=?", (s,)) and self.row(self.q1("SELECT id FROM dms_docs WHERE doc_no=?", (s,))["id"]))
        if not row or row["deleted_at"] or not self.can_read(p, row):
            raise DmsError(404, "연결할 문서를 찾을 수 없습니다")
        return row

    def link_docs(self, p, doc_id, other) -> dict:
        row = self.get_row(p, doc_id, "edit")
        o = self._resolve(p, other)
        if o["id"] == doc_id:
            raise DmsError(400, "자기 자신과는 연결할 수 없습니다")
        a, b = sorted((doc_id, o["id"]))
        self.x("INSERT OR IGNORE INTO doc_links VALUES(?,?,?,?)", (a, b, p.id, now()))
        self.log(p, row, "linked", o["doc_no"])
        return {"related": self.related(p, doc_id)}

    def unlink_docs(self, p, doc_id, other_id) -> dict:
        row = self.get_row(p, doc_id, "edit")
        a, b = sorted((doc_id, int(other_id)))
        if not self.x("DELETE FROM doc_links WHERE a=? AND b=?", (a, b)).rowcount:
            raise DmsError(404, "연결을 찾을 수 없습니다")
        self.log(p, row, "unlinked", str(other_id))
        return {"related": self.related(p, doc_id)}

    def related(self, p, doc_id) -> list[dict]:
        ids = [r["b"] if r["a"] == doc_id else r["a"] for r in self.q("SELECT a,b FROM doc_links WHERE a=? OR b=?", (doc_id, doc_id))]
        return self._docs_from(p, ids)

    # ---- saved searches ----
    def save_search(self, p, name, query: dict) -> dict:
        name = str(name or "").strip()[:60]
        if not name or not isinstance(query, dict):
            raise DmsError(400, "이름과 검색 조건이 필요합니다")
        if self.q1("SELECT COUNT(*) AS n FROM saved_searches WHERE user_id=?", (p.id,))["n"] >= 50:
            raise DmsError(409, "저장된 검색은 최대 50개입니다")
        cur = self.x("INSERT INTO saved_searches(user_id,name,query,ts) VALUES(?,?,?,?)", (p.id, name, json.dumps(query, ensure_ascii=False)[:2000], now()))
        return {"id": cur.lastrowid, "name": name, "query": query}

    def saved_searches(self, p) -> list[dict]:
        return [{"id": r["id"], "name": r["name"], "query": json.loads(r["query"])} for r in
                self.q("SELECT * FROM saved_searches WHERE user_id=? ORDER BY id DESC", (p.id,))]

    def delete_saved_search(self, p, sid) -> dict:
        n = self.x("DELETE FROM saved_searches WHERE id=? AND user_id=?", (sid, p.id)).rowcount
        if not n:
            raise DmsError(404, "저장된 검색을 찾을 수 없습니다")
        return {"deleted": sid}
