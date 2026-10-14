from __future__ import annotations

import calendar as _cal
import re
from datetime import date

from . import automation
from .errors import DmsError


class CalendarMixin:
    """Month view of everything with a date the caller may see: expiries, retention ends, contract due dates found in
    documents and in the business DB, and approval requests waiting for the caller. Every source goes through the same
    permission checks as lists and search - the calendar can never reveal a document or row the caller cannot open."""

    def calendar(self, p, month: str | None = None) -> dict:
        m = re.fullmatch(r"(\d{4})-(\d{2})", str(month or date.today().strftime("%Y-%m")))
        if not m or not 1 <= int(m.group(2)) <= 12:
            raise DmsError(400, "month 는 YYYY-MM 형식입니다")
        y, mo = int(m.group(1)), int(m.group(2))
        first, last = date(y, mo, 1).isoformat(), date(y, mo, _cal.monthrange(y, mo)[1]).isoformat()
        inside = lambda d: bool(d) and first <= d <= last  # noqa: E731
        events: list[dict] = []
        with self.snapshot():
            for row in self.all_rows():
                if not self.can_read(p, row):
                    continue
                doc = {"doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"]}
                if inside(row["expires_at"]):
                    events.append({**doc, "date": row["expires_at"], "kind": "expiry", "label": "만료"})
                if inside(row.get("retention_until")):
                    events.append({**doc, "date": row["retention_until"], "kind": "retention", "label": "보존기간 종료"})
                due = self._due_date(p, row)
                if inside(due):
                    events.append({**doc, "date": due, "kind": "due", "label": "문서상 납기"})
            for t in self.my_tasks(p):
                since = str(t.get("since") or "")[:10]
                if inside(since):
                    events.append({"doc_id": t["doc_id"], "doc_no": t["doc_no"], "title": t["title"], "date": since, "kind": "task", "label": "결재 요청 도착"})
        gw, cfg = self.app.gateway, (self.app.cfg.get("automation") or {}).get("contract_check")
        if gw and cfg and cfg.get("due_col"):
            t = gw.tables.get(cfg["table"], {})
            title_cols = [c for c in (cfg.get("title_col"), "customer_name") if c and c in t.get("columns", {})]
            for r in gw.rows(p, cfg["table"], [t["key"], cfg["due_col"], *title_cols]):
                if inside(str(r[cfg["due_col"]])):
                    events.append({"doc_id": None, "doc_no": str(r[t["key"]]), "title": " ".join(str(r[c]) for c in title_cols) or str(r[t["key"]]),
                                   "date": str(r[cfg["due_col"]]), "kind": "db_due", "label": "계약 납기 (업무DB)", "ref": f"{cfg['table']}:{r[t['key']]}"})
        events.sort(key=lambda e: (e["date"], e["kind"], e["doc_no"] or ""))
        return {"month": f"{y:04d}-{mo:02d}", "first": first, "last": last, "events": events}

    def _due_date(self, p, row):
        """Contract due date written in the document (cached per document version); only for readable effective text."""
        cache = self.__dict__.setdefault("_due_cache", {})
        key = (row["id"], row["updated"], row["current_ver"] if row["origin"] == "managed" else 0)
        if key in cache:
            return cache[key]
        if row["origin"] == "managed":
            vv = self.view_ver(p, row)
            if not vv:
                return None
            text = self._ver(row, vv)["text"]
            ckey = key + (vv,)
        else:
            idx, doc = self.app.engine.index, self.idx_doc(row)
            text = "\n\n".join(c.text for c in sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord))
            ckey = key
        if ckey in cache:
            return cache[ckey]
        due = automation.extract_fields(text).get("due_date")
        cache[ckey] = due
        return due
