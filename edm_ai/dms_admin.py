from __future__ import annotations

import csv
import io
import json
import re

from .dms import now
from .errors import DmsError


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


class AdminMixin:
    def _need_admin(self, p):
        if not self.admin(p):
            raise DmsError(403, "관리자만 사용할 수 있습니다")

    def list_doc_types(self, p) -> list[dict]:
        out = []
        for t in self.q("SELECT * FROM doc_types ORDER BY code"):
            t["approval_required"] = bool(t["approval_required"])
            t["active"] = bool(t["active"])
            t["default_approvers"] = json.loads(t["default_approvers"] or "[]")
            t["fields"] = self.type_fields(t["code"])
            out.append(t)
        return out if self.admin(p) else [t for t in out if t["active"]]

    def save_doc_type(self, p, spec: dict) -> dict:
        self._need_admin(p)
        code = str(spec.get("code", "")).strip().upper()
        if not re.fullmatch(r"[A-Z]{2,6}", code) or code == "EXT":
            raise DmsError(400, "유형 코드는 영문 대문자 2~6자입니다 (EXT 예약)")
        pattern = str(spec.get("pattern") or f"{code}-{{year}}-{{seq:04}}")
        if not re.fullmatch(r"[A-Za-z0-9_\-{}:]{3,60}", pattern):
            raise DmsError(400, "번호 패턴에 허용되지 않는 문자가 있습니다")
        try:
            sample = pattern.format(year=2000, seq=1)
        except (KeyError, IndexError, ValueError):
            raise DmsError(400, "번호 패턴은 {year} 와 {seq} 만 사용할 수 있습니다 (예: CT-{year}-{seq:04})")
        if not sample:
            raise DmsError(400, "번호 패턴이 올바르지 않습니다")
        try:
            years = int(spec.get("retention_years", 0))
        except (TypeError, ValueError):
            raise DmsError(400, "보존 연수가 올바르지 않습니다")
        if not 0 <= years <= 100:
            raise DmsError(400, "보존 연수는 0~100 입니다")
        approvers = [str(a) for a in spec.get("default_approvers", [])]
        if any(a not in self.app.users for a in approvers) or len(approvers) > 10:
            raise DmsError(400, "기본 결재자에 알 수 없는 사용자가 있습니다")
        name = str(spec.get("name", "")).strip()[:60]
        if not name:
            raise DmsError(400, "유형 이름이 필요합니다")
        self.x("INSERT INTO doc_types VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, pattern=excluded.pattern, "
               "retention_years=excluded.retention_years, approval_required=excluded.approval_required, default_approvers=excluded.default_approvers, active=excluded.active",
               (code, name, pattern, years, int(bool(spec.get("approval_required"))), json.dumps(approvers), int(spec.get("active", True))))
        if "fields" in spec:
            self.set_type_fields(p, code, spec["fields"])
        self.store.log_audit(p.id, f"[문서유형 저장] {code}", [], False, "dms")
        return next(t for t in self.list_doc_types(p) if t["code"] == code)

    def audit_search(self, p, user=None, action=None, q=None, frm=None, to=None, limit=200) -> dict:
        self._need_admin(p)
        sql, args = "SELECT id,ts,user_id,question,evidence,refused,generator FROM audit WHERE 1=1", []
        if user:
            sql += " AND user_id=?"
            args.append(str(user))
        if action:
            sql += " AND question LIKE ?"
            args.append(f"[{action}]%")
        if q:
            sql += " AND (question LIKE ? OR evidence LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        if frm:
            sql += " AND ts>=?"
            args.append(self.clean_date(frm, "시작일"))
        if to:
            sql += " AND ts<?"
            args.append(self.clean_date(to, "종료일") + "T99")
        rows = self.q(sql + " ORDER BY id DESC LIMIT ?", (*args, min(int(limit), 5000)))
        ok, bad = self.store.verify_audit()
        return {"chain_ok": ok, "first_bad_id": bad, "count": len(rows),
                "entries": [{"id": r["id"], "ts": r["ts"], "user": r["user_id"], "what": r["question"],
                             "refs": json.loads(r["evidence"]), "refused": bool(r["refused"]), "kind": r["generator"]} for r in rows]}

    def audit_csv(self, p, **f) -> str:
        res = self.audit_search(p, limit=5000, **f)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "time", "user", "what", "refs", "kind"])
        for e in res["entries"]:
            w.writerow([_cell(e["id"]), _cell(e["ts"]), _cell(e["user"]), _cell(e["what"]), _cell(" ".join(e["refs"])), _cell(e["kind"])])
        self.store.log_audit(p.id, "[감사 로그 내보내기]", [], False, "dms")
        return "﻿" + out.getvalue()

    def admin_stats(self, p) -> dict:
        self._need_admin(p)
        one = lambda sql: self.q1(sql)["n"]  # noqa: E731
        return {"documents": one("SELECT COUNT(*) AS n FROM dms_docs WHERE deleted_at IS NULL AND origin='managed'"),
                "external_documents": one("SELECT COUNT(*) AS n FROM dms_docs WHERE origin='crawled'"),
                "versions": one("SELECT COUNT(*) AS n FROM dms_versions"), "storage_bytes": one("SELECT COALESCE(SUM(size),0) AS n FROM dms_versions"),
                "trash": one("SELECT COUNT(*) AS n FROM dms_docs WHERE deleted_at IS NOT NULL"),
                "pending_workflows": one("SELECT COUNT(*) AS n FROM workflows WHERE status='pending'"),
                "legal_holds": one("SELECT COUNT(*) AS n FROM dms_docs WHERE legal_hold=1"),
                "active_shares": one(f"SELECT COUNT(*) AS n FROM shares WHERE revoked=0 AND expires>'{now()}'"),
                "folders": one("SELECT COUNT(*) AS n FROM folders")}
