from __future__ import annotations

import csv
import io
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

from .dms import now
from .errors import DmsError


def _t(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _month_keys(n=12, end=None):
    d = (end or datetime.now(timezone.utc)).replace(day=1)
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return out[::-1]


class ReportsMixin:
    """Management reporting for administrators. Everything is aggregated; titles are shown only for documents the
    administrator may also open, so a report can never be used to read around the 'admin manages, does not read' rule."""

    def report(self, p, days: int = 90) -> dict:
        self._need_admin(p)
        days = max(7, min(int(days or 90), 365))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.snapshot():
            rows = [r for r in self.all_rows() if not r["deleted_at"]]
            managed = [r for r in rows if r["origin"] == "managed"]
            dept_of = lambda uid: (self.app.users[uid].dept or "-") if uid in self.app.users else "시스템"  # noqa: E731
            months = _month_keys()
            by_month = Counter(r["created"][:7] for r in managed)
            types = {t["code"]: t["name"] for t in self.q("SELECT code,name FROM doc_types")}
            types["EXT"] = "외부 문서"
            versions = self.q("SELECT d.owner AS o, SUM(v.size) AS s FROM dms_versions v JOIN dms_docs d ON d.id=v.doc_id GROUP BY d.owner")
            by_dept_storage: Counter = Counter()
            for v in versions:
                by_dept_storage[dept_of(v["o"])] += v["s"] or 0
            readable = {r["id"]: self.can_read(p, r) for r in rows}
            activity = Counter(), Counter()
            for h in self.q("SELECT source,action FROM doc_history WHERE ts>=?", (cutoff,)):
                (activity[1] if h["action"] == "downloaded" else activity[0])[h["source"]] += 1
            by_source = {r["source"]: r for r in rows}

            def top(counter):
                out = []
                for src, n in counter.most_common(10):
                    r = by_source.get(src)
                    if r:
                        out.append({"doc_id": r["id"], "doc_no": r["doc_no"], "title": r["title"] if readable.get(r["id"]) else None, "count": n})
                return out

            soon = (datetime.now(timezone.utc) + timedelta(days=90)).date().isoformat()
            ret = Counter(r["retention_until"][:7] for r in managed if r.get("retention_until") and now()[:10] <= r["retention_until"] <= soon)
            levels = Counter(("L3+" if r["level"] >= 3 else f"L{r['level']}") for r in managed)
        wf = self.q("SELECT * FROM workflows WHERE created>=?", (cutoff,))
        hours = []
        for w in wf:
            a, b = _t(w["created"]), _t(w["completed"])
            if w["status"] == "approved" and a and b:
                hours.append(max(0.0, (b - a).total_seconds() / 3600))
        pend = self.q("SELECT * FROM workflows WHERE status='pending'")
        age = Counter()
        for w in pend:
            a = _t(w["created"])
            d = (datetime.now(timezone.utc) - a).total_seconds() / 86400 if a else 0
            age["~1일" if d < 1 else "1~3일" if d < 3 else "3일 초과"] += 1
        per_approver = Counter(s["approver"] for s in self.q("SELECT s.approver FROM wf_steps s JOIN workflows w ON w.id=s.wf_id WHERE w.status='pending' AND s.status='pending'"))
        ai_rows = self.q("SELECT ts,user_id,refused,generator FROM audit WHERE generator!='dms' AND ts>=?", (cutoff,))
        ai_days = Counter(a["ts"][:10] for a in ai_rows)
        day_keys = [(datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat() for i in range(min(days, 30) - 1, -1, -1)]
        reqs = self.q("SELECT * FROM access_requests WHERE created>=?", (cutoff,))
        rh = [max(0.0, ((_t(r["decided_at"]) - _t(r["created"])).total_seconds() / 3600)) for r in reqs if r["decided_at"] and _t(r["decided_at"]) and _t(r["created"])]
        shares = self.q1(f"SELECT COUNT(*) AS n, COALESCE(SUM(views),0) AS v, COALESCE(SUM(dl_count),0) AS d FROM shares WHERE revoked=0 AND expires>'{now()}'")
        soon7 = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
        exp7 = self.q1(f"SELECT COUNT(*) AS n FROM shares WHERE revoked=0 AND expires>'{now()}' AND expires<'{soon7}'")["n"]
        ai_total = len(ai_rows)
        return {
            "days": days, "generated": now(),
            "kpis": {"documents": len(managed), "external_documents": len(rows) - len(managed), "registered_in_period": sum(1 for r in managed if r["created"] >= cutoff),
                     "approvals_in_period": len(wf), "ai_questions": ai_total,
                     "ai_answer_rate": round(100 * (1 - sum(a["refused"] for a in ai_rows) / ai_total), 1) if ai_total else None,
                     "pending_approvals": len(pend), "legal_holds": sum(1 for r in managed if r["legal_hold"])},
            "registrations_by_month": [{"month": m, "count": by_month.get(m, 0)} for m in months],
            "documents_by_dept": [{"dept": k, "count": n} for k, n in Counter(dept_of(r["owner"]) if r["origin"] == "managed" else "외부 소스" for r in rows).most_common()],
            "documents_by_type": [{"code": k, "name": types.get(k, k), "count": n} for k, n in Counter(r["doc_type"] for r in rows).most_common()],
            "documents_by_status": [{"status": k, "count": n} for k, n in Counter(r["status"] for r in rows).most_common()],
            "documents_by_level": [{"level": k, "count": levels.get(k, 0)} for k in ("L0", "L1", "L2", "L3+")],
            "storage_by_dept": [{"dept": k, "bytes": n} for k, n in by_dept_storage.most_common()],
            "approvals": {"by_status": [{"status": k, "count": n} for k, n in Counter(w["status"] for w in wf).most_common()],
                          "turnaround_hours": {"avg": round(statistics.mean(hours), 1), "median": round(statistics.median(hours), 1), "p90": round(sorted(hours)[int(0.9 * (len(hours) - 1))], 1), "n": len(hours)} if hours else None,
                          "pending_age": [{"bucket": b, "count": age.get(b, 0)} for b in ("~1일", "1~3일", "3일 초과")],
                          "pending_by_approver": [{"user": u, "name": self.app.users[u].name if u in self.app.users else u, "count": n} for u, n in per_approver.most_common(8)]},
            "ai_usage": {"daily": [{"day": d, "count": ai_days.get(d, 0)} for d in day_keys], "users": len({a["user_id"] for a in ai_rows}),
                         "no_evidence": sum(a["refused"] for a in ai_rows), "llm_answers": sum(1 for a in ai_rows if a["generator"] == "llm")},
            "active_documents": top(activity[0]), "most_downloaded": top(activity[1]),
            "retention": {"ending_by_month": [{"month": k, "count": n} for k, n in sorted(ret.items())], "ending_90d": sum(ret.values()),
                          "expiring_30d": sum(1 for r in managed if r["expires_at"] and now()[:10] <= r["expires_at"] <= (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()),
                          "trash": self.q1("SELECT COUNT(*) AS n FROM dms_docs WHERE deleted_at IS NOT NULL")["n"]},
            "access_requests": {"total": len(reqs), "pending": sum(1 for r in reqs if r["status"] == "pending"), "approved": sum(1 for r in reqs if r["status"] == "approved"),
                                "denied": sum(1 for r in reqs if r["status"] == "denied"), "avg_decision_hours": round(statistics.mean(rh), 1) if rh else None},
            "sharing": {"active": shares["n"], "views": shares["v"], "downloads": shares["d"], "expiring_7d": exp7},
        }

    def report_csv(self, p, section: str, days: int = 90) -> str:
        r = self.report(p, days)
        tables = {"registrations": ("월", "등록 건수", [(x["month"], x["count"]) for x in r["registrations_by_month"]]),
                  "departments": ("부서", "문서 수", [(x["dept"], x["count"]) for x in r["documents_by_dept"]]),
                  "types": ("유형", "문서 수", [(x["name"], x["count"]) for x in r["documents_by_type"]]),
                  "ai_daily": ("일자", "AI 질문 수", [(x["day"], x["count"]) for x in r["ai_usage"]["daily"]]),
                  "storage": ("부서", "저장 용량(byte)", [(x["dept"], x["bytes"]) for x in r["storage_by_dept"]]),
                  "retention": ("월", "보존 종료 건수", [(x["month"], x["count"]) for x in r["retention"]["ending_by_month"]])}
        if section not in tables:
            raise DmsError(400, "section 은 " + ", ".join(tables) + " 중 하나입니다")
        a, b, data = tables[section]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([a, b])
        for row in data:
            w.writerow([str(c).lstrip("=+-@") if isinstance(c, str) else c for c in row])         # no formula injection in Excel
        return "﻿" + out.getvalue()
