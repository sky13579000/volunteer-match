from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

from . import dlp
from .dms import now, today
from .errors import DmsError

PERMANENT = 99                       # retention_years value meaning "keep forever"
CODE_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,19}")
DLP_DEFAULT = {"mode": "warn", "block_kinds": ["rrn", "alien", "card", "passport"], "raise_to": 3, "exempt_roles": ["hr"], "no_download": False}


class RecordsMixin:
    """Records management: personal-data detection, classification scheme with retention, and the disposal procedure
    (request -> separate approver -> permanent deletion -> ledger). Content is never shown to records staff."""

    # =============== roles ===============
    def _records(self, p):
        if not (self.admin(p) or "records" in p.roles):
            raise DmsError(403, "기록물관리 담당자 또는 관리자만 사용할 수 있습니다")

    # =============== personal data (DLP) ===============
    def _dlp_cfg(self) -> dict:
        c = self.app.cfg.get("dlp")
        if c is False or (isinstance(c, dict) and c.get("mode") == "off"):
            return {"mode": "off"}
        return {**DLP_DEFAULT, **(c if isinstance(c, dict) else {})}

    def _dlp_gate(self, p, text: str) -> dict:
        """Scan BEFORE the version is accepted. In 'block' mode a document with high-risk identifiers is refused (counts only in the message)."""
        cfg = self._dlp_cfg()
        if cfg["mode"] == "off":
            return {}
        found = dlp.scan(text)
        exempt = bool(set(cfg["exempt_roles"]) & set(p.roles))
        hits = {k: n for k, n in found.items() if k in cfg["block_kinds"]}
        if cfg["mode"] == "block" and hits and not exempt:
            raise DmsError(422, f"개인정보({dlp.summary(hits)})가 포함되어 등록할 수 없습니다. 해당 부분을 지우거나 가린 뒤 다시 올려 주세요")
        return found

    def _dlp_apply(self, p, row, ver: int, found: dict):
        cfg = self._dlp_cfg()
        with self.db:
            self.db.execute("DELETE FROM dlp_findings WHERE doc_id=? AND ver=?", (row["id"], ver))
            for k, n in found.items():
                self.db.execute("INSERT INTO dlp_findings(doc_id,ver,kind,n,ts) VALUES(?,?,?,?,?)", (row["id"], ver, k, n, now()))
        high = {k: n for k, n in found.items() if dlp.SEVERITY[k] == "high"}
        if not high or cfg["mode"] == "off":
            return
        exempt = bool(set(cfg["exempt_roles"]) & set(p.roles))
        note = f"{row['doc_no']} 문서에서 개인정보({dlp.summary(high)})가 검출되었습니다"
        if cfg["mode"] == "raise_level" and not exempt:
            sets, parts = {}, []
            if row["level"] < int(cfg["raise_to"]):
                sets["level"] = int(cfg["raise_to"])
                parts.append(f"보안등급 L{cfg['raise_to']} 로 상향 (L{cfg['raise_to']} 미만은 작성자도 열람 불가)")
            if cfg.get("no_download") and not row["props"].get("no_download"):
                sets["props"] = json.dumps({**row["props"], "no_download": "1"}, ensure_ascii=False)
                parts.append("다운로드 제한")
            if sets:
                self.x(f"UPDATE dms_docs SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), row["id"]))
                row = self.row(row["id"])
                self.reindex(row)
                self.log("system", row, "dlp_protected", ", ".join(parts))
                note += " → " + ", ".join(parts)
        self.notify(row["owner"], "privacy_alert", row["id"], note)
        for uid, u in self.app.users.items():
            if (self.admin(u) or "records" in u.roles) and uid != row["owner"]:
                self.notify(uid, "privacy_alert", None, note.split(" → ")[0])
        self.log("system", row, "dlp_detected", dlp.summary(found))

    def privacy_of(self, p, row) -> dict | None:
        """Findings for the document's newest scanned version; shown only to people who may read the document."""
        if not self.can_read(p, row):
            return None
        rows = self.q("SELECT kind,n,ver FROM dlp_findings WHERE doc_id=? AND ver=(SELECT MAX(ver) FROM dlp_findings WHERE doc_id=?)", (row["id"], row["id"]))
        if not rows:
            return None
        return {"ver": rows[0]["ver"], "findings": dlp.describe({r["kind"]: r["n"] for r in rows}), "mode": self._dlp_cfg()["mode"]}

    def privacy_overview(self, p) -> dict:
        self._records(p)
        out = []
        for r in self.q("SELECT doc_id, GROUP_CONCAT(kind) AS kinds, SUM(n) AS n, MAX(ver) AS ver FROM dlp_findings GROUP BY doc_id ORDER BY SUM(n) DESC"):
            row = self.row(r["doc_id"])
            if not row or row["deleted_at"]:
                continue
            found = {x["kind"]: x["n"] for x in self.q("SELECT kind,n FROM dlp_findings WHERE doc_id=? AND ver=(SELECT MAX(ver) FROM dlp_findings WHERE doc_id=?)", (r["doc_id"], r["doc_id"]))}
            out.append({"doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"] if self.can_read(p, row) else None, "origin": row["origin"], "level": row["level"],
                        "owner": row["owner"], "folder": self.folder_path(row["folder_id"]) if row["folder_id"] else "", "findings": dlp.describe(found),
                        "high": any(dlp.SEVERITY[k] == "high" for k in found)})
        total = Counter_kinds(out)
        return {"items": out, "totals": total, "mode": self._dlp_cfg()["mode"], "documents": len(out)}

    def privacy_scan_all(self, p) -> dict:
        """Re-scan every document (managed: newest version; collected folders: indexed text). Nothing is changed except the findings table."""
        self._records(p)
        n = scanned = 0
        with self.snapshot():
            rows = [r for r in self.all_rows() if not r["deleted_at"]]
        for row in rows:
            if row["origin"] == "managed":
                if not row["current_ver"]:
                    continue
                text, ver = self._ver(row, row["current_ver"])["text"], row["current_ver"]
            else:
                idx, doc = self.app.engine.index, self.idx_doc(row)
                text = "\n\n".join(c.text for c in sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord)) if doc else ""
                ver = 0
            found = dlp.scan(text)
            scanned += 1
            with self.db:
                self.db.execute("DELETE FROM dlp_findings WHERE doc_id=? AND ver=?", (row["id"], ver))
                for k, c_ in found.items():
                    self.db.execute("INSERT INTO dlp_findings(doc_id,ver,kind,n,ts) VALUES(?,?,?,?,?)", (row["id"], ver, k, c_, now()))
            n += 1 if found else 0
        self.store.log_audit(p.id, f"[개인정보 점검] {scanned}건 점검, {n}건 검출", [], False, "dms")
        return {"scanned": scanned, "with_findings": n}

    # =============== classification scheme ===============
    def classifications(self, p=None, include_inactive=False) -> list[dict]:
        rows = self.q("SELECT * FROM classifications" + ("" if include_inactive else " WHERE active=1") + " ORDER BY code")
        used = {r["class_code"]: r["n"] for r in self.q("SELECT class_code, COUNT(*) AS n FROM dms_docs WHERE class_code IS NOT NULL AND deleted_at IS NULL GROUP BY class_code")}
        return [{**r, "active": bool(r["active"]), "documents": used.get(r["code"], 0)} for r in rows]

    def _class(self, code):
        if code in (None, ""):
            return None
        c = self.q1("SELECT * FROM classifications WHERE code=? AND active=1", (str(code),))
        if not c:
            raise DmsError(400, f"알 수 없는 분류입니다: {code}")
        return c

    def _norm_class(self, data: dict, known=None) -> dict:
        code = str(data.get("code") or "").strip()
        name = str(data.get("name") or "").strip()[:100]
        parent = str(data.get("parent") or "").strip() or None
        try:
            years = int(data.get("retention_years", 0) or 0)
        except (TypeError, ValueError):
            raise DmsError(400, "보존연수는 숫자입니다")
        if not CODE_RE.fullmatch(code) or not name:
            raise DmsError(400, "분류 코드(영문·숫자·.-_ 20자 이하)와 이름이 필요합니다")
        if not (0 <= years <= PERMANENT):
            raise DmsError(400, f"보존연수는 0~{PERMANENT - 1}년 또는 {PERMANENT}(영구)입니다")
        if parent and (parent == code or not (parent in (known or set()) or self.q1("SELECT 1 AS x FROM classifications WHERE code=?", (parent,)))):
            raise DmsError(400, f"상위 분류가 올바르지 않습니다: {parent}")
        return {"code": code, "name": name, "parent": parent, "retention_years": years, "description": str(data.get("description") or "")[:300], "active": int(bool(data.get("active", True)))}

    def _write_class(self, c: dict):
        self.x("INSERT INTO classifications(code,name,parent,retention_years,description,active) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(code) DO UPDATE SET name=excluded.name,parent=excluded.parent,retention_years=excluded.retention_years,description=excluded.description,active=excluded.active",
               (c["code"], c["name"], c["parent"], c["retention_years"], c["description"], c["active"]))

    def save_classification(self, p, data: dict) -> dict:
        self._records(p)
        c = self._norm_class(data)
        self._write_class(c)
        self.store.log_audit(p.id, f"[분류 저장] {c['code']} {c['name']} 보존 {c['retention_years']}년", [], False, "dms")
        return next(x for x in self.classifications(include_inactive=True) if x["code"] == c["code"])

    def import_classifications(self, p, text: str) -> dict:
        """CSV: 코드,이름,상위코드,보존연수,설명 (header row optional). All-or-nothing."""
        self._records(p)
        rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if r and any(c.strip() for c in r)]
        if rows and rows[0][0].strip() in ("코드", "code"):
            rows = rows[1:]
        if not rows or len(rows) > 2000:
            raise DmsError(400, "분류 행이 없거나 너무 많습니다 (최대 2000)")
        parsed = []
        for r in rows:
            r = [c.strip() for c in r] + [""] * 5
            parsed.append({"code": r[0], "name": r[1], "parent": r[2], "retention_years": r[3] or 0, "description": r[4]})
        parsed.sort(key=lambda d: (d["code"].count(".") + d["code"].count("-"), d["code"]))
        seen: set = set()
        norm = []
        for d in parsed:                                              # validate EVERYTHING first, then write: a bad row leaves the scheme untouched
            c = self._norm_class(d, seen)
            seen.add(c["code"])
            norm.append(c)
        for c in norm:
            self._write_class(c)
        self.store.log_audit(p.id, f"[분류표 가져오기] {len(norm)}건", [], False, "dms")
        return {"imported": len(norm)}

    # =============== disposal procedure ===============
    def disposal_candidates(self, p) -> list[dict]:
        self._records(p)
        pending = {i["doc_id"] for i in self.q("SELECT i.doc_id FROM disposal_items i JOIN disposals d ON d.id=i.disposal_id WHERE d.status='pending'")}
        out = []
        for row in self.all_rows():
            if row["origin"] != "managed" or row["legal_hold"] or not row["retention_until"] or row["retention_until"] > today() or row["id"] in pending:
                continue
            if self._wf(row["id"]):
                continue
            c = self.q1("SELECT name FROM classifications WHERE code=?", (row["class_code"],)) if row["class_code"] else None
            out.append({"doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"], "class_code": row["class_code"], "class_name": c["name"] if c else None,
                        "folder": self.folder_path(row["folder_id"]) if row["folder_id"] else "", "owner": row["owner"], "retention_until": row["retention_until"], "updated": self._fp(row)})
        return sorted(out, key=lambda x: x["retention_until"])

    @staticmethod
    def _fp(row) -> str:
        """Fingerprint of everything a records officer reviewed; any change after the request voids that item."""
        import hashlib
        keys = ("title", "description", "tags", "props", "class_code", "current_ver", "effective_ver", "level", "entries", "retention_until", "status", "folder_id", "owner")
        return hashlib.sha256(json.dumps([row.get(k) for k in keys], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]

    def request_disposal(self, p, ids, reason: str) -> dict:
        self._records(p)
        reason = str(reason or "").strip()[:500]
        if not reason:
            raise DmsError(400, "폐기 사유를 입력하세요")
        ids = [int(i) for i in (ids or [])][:500]
        if not ids:
            raise DmsError(400, "폐기할 문서를 선택하세요")
        ok = {c["doc_id"]: c for c in self.disposal_candidates(p)}
        bad = [i for i in ids if i not in ok]
        if bad:
            raise DmsError(409, f"폐기 대상이 아닌 문서가 포함되어 있습니다 (보존기간 미경과·법적 보류·결재 중·이미 신청됨): {len(bad)}건")
        cur = self.x("INSERT INTO disposals(status,reason,requested_by,requested_at) VALUES('pending',?,?,?)", (reason, p.id, now()))
        did = cur.lastrowid
        for i in ids:
            c = ok[i]
            self.x("INSERT INTO disposal_items(disposal_id,doc_id,doc_no,title,class_code,folder,owner,retention_until,doc_updated) VALUES(?,?,?,?,?,?,?,?,?)",
                   (did, i, c["doc_no"], c["title"], c["class_code"], c["folder"], c["owner"], c["retention_until"], c["updated"]))
        self.store.log_audit(p.id, f"[폐기 심의 신청] #{did} {len(ids)}건", [], False, "dms")
        for uid, u in self.app.users.items():
            if self.admin(u) and uid != p.id:
                self.notify(uid, "disposal_request", None, f"폐기 심의 신청 #{did}: {len(ids)}건 ({p.name})")
        return {"id": did, "items": len(ids)}

    def list_disposals(self, p) -> list[dict]:
        self._records(p)
        out = []
        for d in self.q("SELECT * FROM disposals ORDER BY id DESC LIMIT 100"):
            items = self.q("SELECT doc_no,title,class_code,folder,retention_until,result FROM disposal_items WHERE disposal_id=? ORDER BY id", (d["id"],))
            out.append({**d, "items": items})
        return out

    def decide_disposal(self, p, disposal_id, approve: bool, note="") -> dict:
        """Only an administrator decides, and never on a request they made themselves (separation of duties)."""
        if not self.admin(p):
            raise DmsError(403, "폐기 승인은 관리자만 할 수 있습니다")
        d = self.q1("SELECT * FROM disposals WHERE id=?", (int(disposal_id),))
        if not d or d["status"] != "pending":
            raise DmsError(404, "심의 건을 찾을 수 없습니다")
        if d["requested_by"] == p.id and not self.app.cfg.get("disposal", {}).get("allow_self_approval"):
            raise DmsError(403, "본인이 신청한 폐기 건은 다른 관리자가 승인해야 합니다")
        note = str(note or "").strip()[:300]
        if not approve:
            self.x("UPDATE disposals SET status='rejected', decided_by=?, decided_at=?, note=? WHERE id=?", (p.id, now(), note, d["id"]))
            self.notify(d["requested_by"], "disposal_result", None, f"폐기 심의 #{d['id']} 가 반려되었습니다" + (f": {note}" if note else ""))
            self.store.log_audit(p.id, f"[폐기 심의 반려] #{d['id']}", [], False, "dms")
            return {"id": d["id"], "status": "rejected"}
        done = skipped = 0
        for it in self.q("SELECT * FROM disposal_items WHERE disposal_id=? ORDER BY id", (d["id"],)):
            row = self.row(it["doc_id"])
            why = None
            if not row or row["doc_no"] != it["doc_no"]:
                why = "문서가 이미 없습니다"
            elif self._fp(row) != it["doc_updated"]:
                why = "신청 이후 문서가 변경되었습니다"
            elif row["legal_hold"] or (row["retention_until"] and row["retention_until"] > today()):
                why = "법적 보류 또는 보존기간 연장"
            elif self._wf(row["id"]):
                why = "결재 진행 중"
            if why:
                self.x("UPDATE disposal_items SET result=? WHERE id=?", ("제외: " + why, it["id"]))
                skipped += 1
                continue
            last = self.q1("SELECT sha256, COUNT(*) AS n FROM dms_versions WHERE doc_id=?", (row["id"],)) or {}
            sha = (self.q1("SELECT sha256 FROM dms_versions WHERE doc_id=? ORDER BY ver DESC LIMIT 1", (row["id"],)) or {}).get("sha256")
            self.x("INSERT INTO disposal_ledger(disposal_id,doc_no,title,class_code,folder,owner,retention_until,versions,sha256,disposed_at,requested_by,approved_by,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (d["id"], it["doc_no"], it["title"], it["class_code"], it["folder"], it["owner"], it["retention_until"], last.get("n", 0), sha, now(), d["requested_by"], p.id, d["reason"]))
            self._purge_row(row, p.id)
            self.x("UPDATE disposal_items SET result='폐기 완료' WHERE id=?", (it["id"],))
            done += 1
        self.x("UPDATE disposals SET status='approved', decided_by=?, decided_at=?, note=? WHERE id=?", (p.id, now(), note, d["id"]))
        self.notify(d["requested_by"], "disposal_result", None, f"폐기 심의 #{d['id']} 승인: {done}건 폐기, {skipped}건 제외")
        self.app.events.emit("disposal.completed", None, p.id, f"#{d['id']} 폐기 {done}건, 제외 {skipped}건")
        self.store.log_audit(p.id, f"[폐기 승인] #{d['id']} {done}건 폐기, {skipped}건 제외", [], False, "dms")
        return {"id": d["id"], "status": "approved", "disposed": done, "skipped": skipped}

    def disposal_ledger(self, p) -> list[dict]:
        self._records(p)
        return self.q("SELECT * FROM disposal_ledger ORDER BY id DESC LIMIT 2000")

    def disposal_ledger_csv(self, p) -> str:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["폐기번호", "문서번호", "제목", "분류", "폴더", "소유자", "보존만료일", "버전수", "최종 SHA-256", "폐기일시", "신청자", "승인자", "사유"])
        for r in self.disposal_ledger(p):
            w.writerow([str(c).lstrip("=+-@") if isinstance(c, str) else c for c in (r["disposal_id"], r["doc_no"], r["title"], r["class_code"] or "", r["folder"], r["owner"], r["retention_until"],
                                                                                     r["versions"], r["sha256"] or "", r["disposed_at"], r["requested_by"], r["approved_by"], r["reason"])])
        return "﻿" + out.getvalue()


def Counter_kinds(items) -> list[dict]:
    from collections import Counter
    c = Counter()
    for i in items:
        for f in i["findings"]:
            c[f["kind"]] += f["count"]
    return [{"kind": k, "label": dlp.LABEL[k], "count": n, "severity": dlp.SEVERITY[k]} for k, n in c.most_common()]
