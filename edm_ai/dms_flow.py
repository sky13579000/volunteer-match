from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime

from .dms import now, today
from .errors import DmsError

FIELD_TYPES = ("text", "number", "date", "select")
KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,29}")


class FlowMixin:
    """Per-type custom fields, list export, approval-line templates, CC (참조) and absence / deputy (부재·대결)."""

    # =============== custom fields per document type ===============
    def type_fields(self, code: str) -> list[dict]:
        r = self.q1("SELECT fields FROM type_fields WHERE code=?", (code,))
        return json.loads(r["fields"]) if r else []

    def set_type_fields(self, p, code: str, fields) -> list[dict]:
        self._need_admin(p)
        if not self.q1("SELECT 1 AS x FROM doc_types WHERE code=?", (code,)):
            raise DmsError(404, "문서유형을 찾을 수 없습니다")
        if not isinstance(fields, list) or len(fields) > 20:
            raise DmsError(400, "항목은 20개까지 정의할 수 있습니다")
        out, seen = [], set()
        for f in fields:
            key, label, typ = str(f.get("key", "")).strip(), str(f.get("label", "")).strip()[:40], str(f.get("type", "text"))
            if not KEY_RE.fullmatch(key) or key in seen:
                raise DmsError(400, f"항목 키가 올바르지 않거나 중복입니다: {key or '(비어 있음)'} (영문으로 시작, 영문·숫자·_ 30자)")
            if not label or typ not in FIELD_TYPES:
                raise DmsError(400, f"항목 이름/형식이 올바르지 않습니다: {key}")
            opts = [str(o).strip()[:60] for o in (f.get("options") or []) if str(o).strip()][:30]
            if typ == "select" and not opts:
                raise DmsError(400, f"선택 항목에는 보기(options)가 필요합니다: {key}")
            seen.add(key)
            out.append({"key": key, "label": label, "type": typ, "required": bool(f.get("required")), "options": opts if typ == "select" else []})
        self.x("INSERT INTO type_fields(code,fields) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET fields=excluded.fields", (code, json.dumps(out, ensure_ascii=False)))
        self.store.log_audit(p.id, f"[문서유형 항목 정의] {code} {len(out)}개", [], False, "dms")
        return out

    def check_fields(self, code: str, props: dict, partial=False):
        for f in self.type_fields(code):
            if f["key"] not in props:
                if f["required"] and not partial:
                    raise DmsError(400, f"필수 항목을 입력하세요: {f['label']}")
                continue
            v = str(props[f["key"]]).strip()
            if not v:
                if f["required"]:
                    raise DmsError(400, f"필수 항목을 입력하세요: {f['label']}")
                continue
            if f["type"] == "number":
                try:
                    float(v.replace(",", ""))
                except ValueError:
                    raise DmsError(400, f"숫자로 입력하세요: {f['label']}")
            elif f["type"] == "date":
                try:
                    datetime.fromisoformat(v[:10])
                except ValueError:
                    raise DmsError(400, f"날짜(YYYY-MM-DD)로 입력하세요: {f['label']}")
            elif f["type"] == "select" and v not in f["options"]:
                raise DmsError(400, f"보기에 없는 값입니다: {f['label']}")

    # =============== list export ===============
    def docs_csv(self, p, **flt) -> str:
        items, page = [], 1
        while len(items) < 5000:
            r = self.search(p, flt.get("q"), **{k: v for k, v in flt.items() if k != "q"}, page=page, size=200)
            items += r["items"]
            if len(r["items"]) < 200:
                break
            page += 1
        fields = self.type_fields(flt["type"]) if flt.get("type") else []
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["문서번호", "제목", "유형", "상태", "소유자", "폴더", "수정일", "보안등급", "태그", "보존만료"] + [f["label"] for f in fields])
        for d in items:
            if not d["readable"]:
                continue
            props = (self.row(d["id"]) or {}).get("props") or {}
            vals = [d["doc_no"], d["title"], d["doc_type_name"], d["status"], d["owner"], d["folder_path"], d["updated"][:10], d["level"], ",".join(d["tags"]), d["retention_until"] or ""] + [props.get(f["key"], "") for f in fields]
            w.writerow([str(c).lstrip("=+-@") if isinstance(c, str) else c for c in vals])
        self.store.log_audit(p.id, f"[목록 내보내기] {len(items)}건", [], False, "dms")
        return "﻿" + out.getvalue()

    # =============== approval-line templates ===============
    def approval_templates(self, p) -> list[dict]:
        out = []
        for t in self.q("SELECT * FROM approval_templates WHERE owner=? OR shared=1 ORDER BY name", (p.id,)):
            t["approvers"], t["cc"] = json.loads(t["approvers"]), json.loads(t["cc"] or "[]")
            t["mine"] = t["owner"] == p.id
            t["shared"] = bool(t["shared"])
            out.append(t)
        return out

    def save_approval_template(self, p, spec: dict) -> dict:
        name = str(spec.get("name") or "").strip()[:60]
        approvers = [str(a) for a in (spec.get("approvers") or [])]
        cc = [str(a) for a in (spec.get("cc") or [])]
        mode = spec.get("mode", "sequential")
        if not name or not approvers or len(approvers) > 10 or len(set(approvers)) != len(approvers) or mode not in ("sequential", "parallel"):
            raise DmsError(400, "이름과 결재자(1~10명, 중복 없음)가 필요합니다")
        if any(a not in self.app.users for a in approvers + cc) or len(cc) > 20:
            raise DmsError(400, "알 수 없는 사용자가 포함되어 있습니다")
        shared = int(bool(spec.get("shared")) and self.admin(p))
        if str(spec.get("id") or "").isdigit():
            t = self.q1("SELECT owner FROM approval_templates WHERE id=?", (int(spec["id"]),))
            if not t or (t["owner"] != p.id and not self.admin(p)):
                raise DmsError(404, "결재선을 찾을 수 없습니다")
            self.x("UPDATE approval_templates SET name=?, approvers=?, cc=?, mode=?, shared=? WHERE id=?", (name, json.dumps(approvers), json.dumps(cc), mode, shared, int(spec["id"])))
            return {"id": int(spec["id"])}
        cur = self.x("INSERT INTO approval_templates(name,owner,approvers,cc,mode,shared) VALUES(?,?,?,?,?,?)", (name, p.id, json.dumps(approvers), json.dumps(cc), mode, shared))
        return {"id": cur.lastrowid}

    def delete_approval_template(self, p, tid) -> dict:
        t = self.q1("SELECT owner FROM approval_templates WHERE id=?", (int(tid),))
        if not t or (t["owner"] != p.id and not self.admin(p)):
            raise DmsError(404, "결재선을 찾을 수 없습니다")
        self.x("DELETE FROM approval_templates WHERE id=?", (int(tid),))
        return {"deleted": int(tid)}

    # =============== absence / deputy ===============
    def my_absence(self, p) -> dict | None:
        return self.q1("SELECT * FROM absences WHERE user_id=?", (p.id,))

    def set_absence(self, p, deputy: str, frm: str, to: str, reason="") -> dict:
        frm, to = self.clean_date(frm, "시작일"), self.clean_date(to, "종료일")
        if not frm or not to or to < frm or (datetime.fromisoformat(to) - datetime.fromisoformat(frm)).days > 180:
            raise DmsError(400, "부재 기간이 올바르지 않습니다 (최대 180일)")
        if deputy not in self.app.users or deputy == p.id:
            raise DmsError(400, "대결자를 선택하세요")
        self.x("INSERT INTO absences(user_id,deputy,frm,to_,reason) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET deputy=excluded.deputy, frm=excluded.frm, to_=excluded.to_, reason=excluded.reason",
               (p.id, deputy, frm, to, str(reason)[:100]))
        n = self.apply_absences()
        self.store.log_audit(p.id, f"[부재 설정] 대결 {deputy} {frm}~{to}", [], False, "dms")
        return {"deputy": deputy, "from": frm, "to": to, "reassigned": n}

    def clear_absence(self, p) -> dict:
        self.x("DELETE FROM absences WHERE user_id=?", (p.id,))
        return {"cleared": True}

    def apply_absences(self) -> int:
        """Hand every ACTIVE pending step of an absent approver to the deputy (recorded as a delegation, so the history shows who acted for whom)."""
        n, t0 = 0, today()
        absent = {a["user_id"]: a for a in self.q("SELECT * FROM absences WHERE frm<=? AND to_>=?", (t0, t0))}
        if not absent:
            return 0
        for w in self.q("SELECT * FROM workflows WHERE status='pending'"):
            w["steps"] = self.q("SELECT * FROM wf_steps WHERE wf_id=? ORDER BY ord, id", (w["id"],))
            row = self.row(w["doc_id"])
            for s in self._active_steps(w):
                a = absent.get(s["approver"])
                if not a or not row:
                    continue
                dep = a["deputy"]
                if dep in (w["requester"], row["owner"]) or any(x["approver"] == dep and x["status"] == "pending" for x in w["steps"]) or dep in absent:
                    continue
                self.x("UPDATE wf_steps SET approver=?, delegated_from=? WHERE id=?", (dep, s["approver"], s["id"]))
                self.reindex_doc(w["doc_id"])
                self.notify(dep, "approval_request", w["doc_id"], f"결재 대결: {row['title']} ({row['doc_no']}) — {s['approver']} 님 부재")
                self.notify(s["approver"], "approval_request", w["doc_id"], f"부재 중이라 {dep} 님이 대결합니다: {row['doc_no']}")
                n += 1
        return n
