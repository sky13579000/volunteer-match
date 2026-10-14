from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import automation
from .dms import now, today
from .errors import DmsError
from .text import tokenize

_WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")


class SearchMixin:
    # ---------- external folder documents appear in the same list ----------
    def sync_crawled(self):
        """Mirror crawled files into dms_docs so they are listed, filtered, commented and favourited like managed ones."""
        docs = self.q("SELECT id,source,title FROM documents")
        crawled = {d["source"]: d for d in docs if not d["source"].startswith("dms/")}
        have = {r["source"]: r for r in self.q("SELECT id,source FROM dms_docs WHERE origin='crawled'")}
        ts = now()
        for src, d in crawled.items():
            parts = src.split("/")
            fid = self.ensure_folder_path("/".join(["외부 소스"] + parts[:-1]), origin="crawled")
            if src in have:
                self.x("UPDATE dms_docs SET title=?, folder_id=?, updated=? WHERE id=? AND (title<>? OR folder_id<>?)",
                       (d["title"], fid, ts, have[src]["id"], d["title"], fid))
            else:
                no = "EXT-" + hashlib.sha1(src.encode()).hexdigest()[:8].upper()
                self.x("INSERT OR IGNORE INTO dms_docs(doc_no,source,title,folder_id,doc_type,origin,owner,created,updated,status) "
                       "VALUES(?,?,?,?,?,'crawled','system',?,?,'approved')", (no, src, d["title"], fid, "EXT", ts, ts))
        for src, r in have.items():
            if src not in crawled:
                with self.db:
                    for t in ("comments", "favorites", "recent", "notifications"):
                        self.db.execute(f"DELETE FROM {t} WHERE doc_id=?", (r["id"],))
                    self.db.execute("DELETE FROM dms_docs WHERE id=?", (r["id"],))

    def reindex_all(self):
        with self.batch():
            for r in self.q("SELECT id FROM dms_docs WHERE origin='managed'"):
                self.reindex_doc(r["id"], reload=False)
            self._changed()

    # ---------- search ----------
    def _descendants(self, fid) -> set[int]:
        out, frontier = {fid}, [fid]
        while frontier:
            kids = [r["id"] for r in self.q(f"SELECT id FROM folders WHERE parent_id IN ({','.join('?' * len(frontier))})", frontier)]
            frontier = [k for k in kids if k not in out]
            out |= set(frontier)
        return out

    @staticmethod
    def _snippet(text: str, q: str, width=90) -> dict | None:
        terms = sorted({w.lower() for w in _WORD.findall(q)}, key=len, reverse=True)
        low = text.lower()
        pos = [(low.find(t), t) for t in terms if low.find(t) >= 0]
        if not pos:
            return {"text": text[:width * 2].replace("\n", " "), "hits": []}
        start = max(0, min(p for p, _ in pos) - width // 2)
        win = text[start:start + width * 2].replace("\n", " ")
        wl = win.lower()
        hits = []
        for t in terms:
            i = wl.find(t)
            while i >= 0 and len(hits) < 12:
                hits.append([i, i + len(t)])
                i = wl.find(t, i + len(t))
        return {"text": ("…" if start else "") + win, "hits": [[a + (1 if start else 0), b + (1 if start else 0)] for a, b in hits]}

    def search(self, p, q=None, *, type=None, status=None, tag=None, folder=None, owner=None, origin=None, date_from=None,
               date_to=None, mine=False, favorites=False, sort=None, page=1, size=30) -> dict:
        q = (q or "").strip()[:300] or None
        with self.snapshot():
            return self._search(p, q, type, status, tag, folder, owner, origin, date_from, date_to, mine, favorites, sort, page, size)

    def _search(self, p, q, type, status, tag, folder, owner, origin, date_from, date_to, mine, favorites, sort, page, size) -> dict:
        q = (q or "").strip()[:300] or None
        rows = self.all_rows()
        fav = {r["doc_id"] for r in self.q("SELECT doc_id FROM favorites WHERE user_id=?", (p.id,))} if favorites else set()
        dfrom, dto = self.clean_date(date_from, "시작일"), self.clean_date(date_to, "종료일")
        sub = self._descendants(int(folder)) if folder else None
        hits: dict[str, tuple[int, dict | None]] = {}
        if q:
            idx = self.app.engine.index
            ents = self.app.extractor.extract(q)
            for rank, (c, _) in enumerate(idx.search(p, q, ents, k=500, per_doc=1)):
                hits[idx.docs[c.doc_id].source] = (rank, self._snippet(c.text, q))
        ql = (q or "").lower()
        out = []
        for row in rows:
            readable = self.can_read(p, row)
            if not readable and not (self.admin(p) and not q):
                continue
            if type and row["doc_type"] != type or status and row["status"] != status or origin and row["origin"] != origin:
                continue
            if tag and tag not in row["tags"] or owner and row["owner"] != owner or mine and row["owner"] != p.id:
                continue
            if sub is not None and row["folder_id"] not in sub or favorites and row["id"] not in fav:
                continue
            if dfrom and row["updated"][:10] < dfrom or dto and row["updated"][:10] > dto:
                continue
            rank, snip = hits.get(row["source"], (None, None))
            if q:
                meta_hit = ql in row["title"].lower() or ql in row["doc_no"].lower() or any(ql == t.lower() for t in row["tags"])
                if rank is None and not (meta_hit and readable):
                    continue
            d = self.summary(p, row, with_perms=False)
            d["snippet"] = snip
            d["_rank"] = rank if rank is not None else 10_000
            out.append(d)
        key = {"updated": lambda d: d["updated"], "title": lambda d: d["title"], "doc_no": lambda d: d["doc_no"]}
        if sort in key:
            out.sort(key=key[sort], reverse=sort == "updated")
        elif q:
            out.sort(key=lambda d: d["_rank"])
        else:
            out.sort(key=lambda d: d["updated"], reverse=True)
        for d in out:
            d.pop("_rank")
        facets = {"type": Counter(d["doc_type"] for d in out), "status": Counter(d["status"] for d in out),
                  "tag": Counter(t for d in out for t in d["tags"]), "owner": Counter(d["owner"] for d in out if d["owner"])}
        page, size = max(1, int(page)), max(1, min(int(size), 100))
        return {"total": len(out), "page": page, "size": size, "items": out[(page - 1) * size:page * size],
                "facets": {k: [{"value": v, "count": n} for v, n in c.most_common(20)] for k, c in facets.items()}}

    # ---------- dashboard ----------
    def dashboard(self, p) -> dict:
        with self.snapshot():
            return self._dashboard(p)

    def _dashboard(self, p) -> dict:
        rows = self.all_rows()
        readable = [r for r in rows if self.can_read(p, r)]
        types = {t["code"]: t["name"] for t in self.q("SELECT code,name FROM doc_types")}
        types["EXT"] = "외부 문서"
        warn = (datetime.now(timezone.utc) + timedelta(days=self.warn_days)).date().isoformat()
        mine = [r for r in rows if r["owner"] == p.id]
        expiring = [r for r in readable if r["expires_at"] and today() <= r["expires_at"] <= warn and ("edit" in self.perms(p, r) or r["owner"] == p.id)]
        tasks = self.my_tasks(p)
        own_ids = [r["id"] for r in mine]
        size = self.q1(f"SELECT COALESCE(SUM(size),0) AS n FROM dms_versions" + ("" if self.admin(p) else f" WHERE doc_id IN ({','.join('?' * len(own_ids))})" if own_ids else " WHERE 0"),
                       () if self.admin(p) else tuple(own_ids))["n"]
        return {
            "counts": {"readable": len(readable), "mine": len(mine), "drafts": sum(r["status"] == "draft" for r in mine),
                       "in_review": sum(r["status"] == "in_review" for r in mine), "expiring_soon": len(expiring),
                       "tasks": len(tasks), "unread": self.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0", (p.id,))["n"]},
            "by_type": [{"code": c, "name": types.get(c, c), "count": n} for c, n in Counter(r["doc_type"] for r in readable).most_common()],
            "by_status": [{"status": s, "count": n} for s, n in Counter(r["status"] for r in readable).most_common()],
            "tasks": tasks[:5], "requests": self.my_requests(p)[:5],
            "expiring": [self.summary(p, r, False) for r in sorted(expiring, key=lambda r: r["expires_at"])[:5]],
            "recent": self.recent(p, 6), "favorites": self.favorites(p)[:6],
            "storage_bytes": size, "storage_scope": "all" if self.admin(p) else "mine",
        }

    def ask_doc(self, p, doc_id, question) -> dict:
        """Question answered from ONE document only. The scope narrows the caller's normal permissions; it cannot widen them."""
        row = self.get_row(p, doc_id, "read")
        q = str(question or "").strip()[:2000]
        if not q:
            raise DmsError(400, "질문을 입력하세요")
        if row["origin"] == "managed" and not self.view_ver(p, row):
            raise DmsError(404, "문서를 찾을 수 없습니다")
        ans = self.app.engine.ask(p, q, only_source=row["source"])
        out = ans.to_dict()
        out["doc"] = {"id": row["id"], "doc_no": row["doc_no"], "title": row["title"]}
        return out

    # ---------- document <-> DB cross-check alerts ----------
    def alerts(self, p) -> list[dict]:
        out = []
        auto = (self.app.cfg.get("automation") or {}).get("contract_check")
        for row in sorted(self.all_rows(), key=lambda r: r["source"]):
            if not self.can_read(p, row):
                continue
            pr = self.perms(p, row)
            vv = self.view_ver(p, row, pr) if row["origin"] == "managed" else None
            if row["origin"] == "managed":
                if not vv:
                    continue
                text = self._ver(row, vv)["text"]
            else:
                idx, doc = self.app.engine.index, self.idx_doc(row)
                text = "\n\n".join(c.text for c in sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord))
            a = automation.analyze(p, row["title"], text, self.app.gateway, auto)
            if a["findings"]:
                out.append({"doc_id": row["id"], "doc_no": row["doc_no"], "source": row["source"], "title": row["title"],
                            "category": a["category"], "findings": a["findings"]})
        return out
