from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import dlp
from .dms import now, today
from .errors import DmsError

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
REG_FIELDS = ("name", "purpose", "legal_basis", "items", "subjects", "retention", "third_party", "outsourced", "manager", "location")


def wipe_tree(root: Path) -> int:
    """Best-effort secure delete: overwrite every file with random bytes, then zeros, before unlinking. On SSDs, journaling or copy-on-write
    file systems and on backups this cannot be guaranteed - for that, use an encrypted volume and destroy the key (docs/SECURITY.md)."""
    n = 0
    if not root.exists():
        return 0
    for f in sorted(root.rglob("*")):
        if f.is_file() and not f.is_symlink():
            try:
                size = f.stat().st_size
                with open(f, "r+b") as fh:
                    for fill in (os.urandom, lambda k: b"\0" * k):
                        fh.seek(0)
                        left = size
                        while left > 0:
                            step = min(left, 1 << 20)
                            fh.write(fill(step))
                            left -= step
                        fh.flush()
                        os.fsync(fh.fileno())
                n += 1
            except OSError:
                pass
    shutil.rmtree(root, ignore_errors=True)
    return n


class PrivacyMixin:
    """Personal-data register (개인정보파일 대장), destruction deadlines, reason-logged unmasking, anonymisation and destruction certificates."""

    # =============== register ===============
    def _pii_clean(self, data: dict) -> dict:
        out = {k: str(data.get(k) or "").strip()[:400] for k in REG_FIELDS}
        if not out["name"] or not out["purpose"]:
            raise DmsError(400, "개인정보파일 이름과 처리 목적은 필수입니다")
        by = str(data.get("destroy_by") or "").strip()
        if by and not DATE_RE.fullmatch(by):
            raise DmsError(400, "파기 예정일은 YYYY-MM-DD 형식이어야 합니다")
        out["destroy_by"] = by or None
        out["folder_id"] = int(data["folder_id"]) if str(data.get("folder_id") or "").isdigit() else None
        return out

    def pii_registry(self, p) -> dict:
        self._records(p)
        t0, soon = today(), (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        items = []
        for r in self.q("SELECT * FROM pii_registry WHERE status='active' ORDER BY COALESCE(destroy_by,'9999'), id"):
            due = "overdue" if r["destroy_by"] and r["destroy_by"] < t0 else "soon" if r["destroy_by"] and r["destroy_by"] <= soon else None
            items.append({**r, "due": due, "folder": self.folder_path(r["folder_id"]) if r["folder_id"] else ""})
        return {"items": items, "overdue": sum(1 for i in items if i["due"] == "overdue"), "soon": sum(1 for i in items if i["due"] == "soon")}

    def pii_registry_save(self, p, data: dict) -> dict:
        self._records(p)
        c = self._pii_clean(data)
        cols = ", ".join(c)
        if str(data.get("id") or "").isdigit():
            rid = int(data["id"])
            if not self.q1("SELECT 1 AS x FROM pii_registry WHERE id=?", (rid,)):
                raise DmsError(404, "대장 항목을 찾을 수 없습니다")
            self.x(f"UPDATE pii_registry SET {', '.join(k + '=?' for k in c)}, updated=?, notified=NULL WHERE id=?", (*c.values(), now(), rid))
        else:
            rid = self.x(f"INSERT INTO pii_registry({cols}, status, created_by, created, updated) VALUES({', '.join('?' * len(c))}, 'active', ?, ?, ?)", (*c.values(), p.id, now(), now())).lastrowid
        self.store.log_audit(p.id, f"[개인정보파일 대장] #{rid} {c['name']}", [], False, "dms")
        return {"id": rid}

    def pii_registry_delete(self, p, rid) -> dict:
        self._records(p)
        if not self.q1("SELECT 1 AS x FROM pii_registry WHERE id=?", (int(rid),)):
            raise DmsError(404, "대장 항목을 찾을 수 없습니다")
        self.x("UPDATE pii_registry SET status='closed', updated=? WHERE id=?", (now(), int(rid)))
        self.store.log_audit(p.id, f"[개인정보파일 대장 폐쇄] #{rid}", [], False, "dms")
        return {"closed": int(rid)}

    def pii_registry_csv(self, p) -> str:
        import csv
        import io
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["번호", "개인정보파일명", "처리 목적", "근거", "항목", "정보주체", "보유기간", "파기 예정일", "제3자 제공", "위탁", "보관 위치", "담당자", "상태"])
        for r in self.pii_registry(p)["items"]:
            w.writerow([str(c).lstrip("=+-@") if isinstance(c, str) else c for c in (r["id"], r["name"], r["purpose"], r["legal_basis"], r["items"], r["subjects"], r["retention"], r["destroy_by"] or "", r["third_party"], r["outsourced"], r["location"], r["manager"], {"overdue": "기한 경과", "soon": "30일 이내"}.get(r["due"], "정상"))])
        return "﻿" + out.getvalue()

    def privacy_maintenance(self) -> int:
        """Daily: tell the records officers about register entries that are overdue or due within 30 days (once per entry per day)."""
        n, t0 = 0, today()
        soon = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        staff = [uid for uid, u in self.app.users.items() if self.admin(u) or "records" in u.roles]
        for r in self.q("SELECT * FROM pii_registry WHERE status='active' AND destroy_by IS NOT NULL AND destroy_by<=? AND COALESCE(notified,'')<>?", (soon, t0)):
            txt = f"개인정보파일 '{r['name']}' 파기 {'기한이 지났습니다' if r['destroy_by'] < t0 else '예정일이 가깝습니다'} ({r['destroy_by']})"
            for uid in staff:
                self.notify(uid, "privacy_due", None, txt)
            self.x("UPDATE pii_registry SET notified=? WHERE id=?", (t0, r["id"]))
            n += 1
        return n

    # =============== unmask with reason ===============
    def unmask(self, p, doc_id, reason: str) -> dict:
        """The screen shows personal data masked. A reader may ask for the original; a reason is mandatory and the request is logged."""
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise DmsError(400, "원문 열람 사유를 5자 이상 입력하세요")
        roles = (self.app.cfg.get("privacy") or {}).get("unmask_roles")
        if roles and not (set(roles) & set(p.roles)) and not self.admin(p):
            raise DmsError(403, "원문 열람 권한이 없습니다 (관리자에게 문의)")
        row = self.get_row(p, doc_id, "read")
        if (row["props"] or {}).get("no_download") and not self.admin(p) and row["owner"] != p.id:
            raise DmsError(403, "다운로드가 제한된 문서의 원문은 볼 수 없습니다")
        vv = self.view_ver(p, row)
        if row["origin"] == "crawled":
            idx, doc = self.app.engine.index, self.idx_doc(row)
            text = "\n\n".join(c.text for c in sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord)) if doc else ""
        else:
            text = self._ver(row, vv)["text"] if vv else ""
        self.x("INSERT INTO pii_access_log(doc_id,doc_no,user_id,action,reason,ts) VALUES(?,?,?,?,?,?)", (row["id"], row["doc_no"], p.id, "unmask", reason[:300], now()))
        self.log(p, row, "pii_unmasked", reason[:200])
        if row["owner"] != p.id:
            self.notify(row["owner"], "privacy_alert", row["id"], f"{row['doc_no']} 원문이 열람되었습니다 ({p.name}: {reason[:60]})")
        return {"id": row["id"], "text": text}

    def unmask_log(self, p, limit=500) -> list[dict]:
        self._records(p)
        out = []
        for r in self.q("SELECT * FROM pii_access_log ORDER BY id DESC LIMIT ?", (int(limit),)):
            row = self.row(r["doc_id"])
            out.append({**r, "title": row["title"] if row and self.can_read(p, row) else None, "user_name": self.app.users[r["user_id"]].name if r["user_id"] in self.app.users else r["user_id"]})
        return out

    # =============== anonymisation (비식별화) ===============
    def _anonymize_doc(self, row, by: str) -> str:
        """Replace the document by one masked text version; every earlier version is overwritten and removed. Returns the new SHA-256."""
        ver = self._ver(row, row["current_ver"])
        masked = dlp.mask(ver["text"])
        stem = Path(ver["filename"]).stem[:80] or "document"
        name = f"{stem}_비식별.txt"
        data = masked.encode("utf-8")
        did, ts = row["id"], now()
        old = self.q("SELECT ver FROM dms_versions WHERE doc_id=?", (did,))
        for o in old:
            wipe_tree(self.repo / str(did) / str(o["ver"]))
        rel, sha = self._store_file(did, 1, name, data)
        props = {**(row["props"] or {}), "anonymized": today()}
        with self.db:
            self.db.execute("DELETE FROM dms_versions WHERE doc_id=?", (did,))
            self.db.execute("INSERT INTO dms_versions(doc_id,ver,filename,size,sha256,path,status,created_by,created,comment,text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (did, 1, name, len(data), sha, rel, "approved", by, ts, "비식별화 (개인정보 가림)", masked))
            self.db.execute("UPDATE dms_docs SET current_ver=1, effective_ver=1, status='approved', props=?, updated=?, locked_by=NULL, locked_at=NULL WHERE id=?",
                            (json.dumps(props, ensure_ascii=False), ts, did))
            self.db.execute("DELETE FROM dlp_findings WHERE doc_id=?", (did,))
            self.db.execute("DELETE FROM shares WHERE doc_id=?", (did,))
        row2 = self.row(did)
        self.reindex(row2)
        for k, n in dlp.scan(masked).items():
            self.x("INSERT OR REPLACE INTO dlp_findings(doc_id,ver,kind,n,ts) VALUES(?,?,?,?,?)", (did, 1, k, n, ts))
        self.log("system", row2, "anonymized", f"{by} · {sha[:12]}")
        return sha

    # =============== destruction certificate ===============
    def disposal_certificate(self, p, disposal_id) -> dict:
        self._records(p)
        d = self.q1("SELECT * FROM disposals WHERE id=?", (int(disposal_id),))
        if not d or d["status"] != "approved":
            raise DmsError(404, "승인 완료된 폐기 건이 아닙니다")
        rows = self.q("SELECT doc_no,title,class_code,folder,owner,retention_until,versions,sha256,disposed_at,method FROM disposal_ledger WHERE disposal_id=? ORDER BY id", (d["id"],))
        skipped = self.q("SELECT doc_no,title,result FROM disposal_items WHERE disposal_id=? AND result LIKE '제외%'", (d["id"],))
        no = f"DEST-{(d['decided_at'] or now())[:4]}-{d['id']:05d}"
        body = json.dumps([no, d["reason"], d["requested_by"], d["decided_by"], d["decided_at"], rows], sort_keys=True, ensure_ascii=False)
        wipe = bool((self.app.cfg.get("disposal") or {}).get("secure_wipe", True))
        return {"no": no, "disposal_id": d["id"], "method": d.get("method") or "delete", "reason": d["reason"], "requested_by": d["requested_by"], "requested_at": d["requested_at"],
                "approved_by": d["decided_by"], "approved_at": d["decided_at"], "note": d["note"], "items": rows, "skipped": skipped, "secure_wipe": wipe,
                "organization": (self.app.cfg.get("branding") or {}).get("org") or (self.app.cfg.get("branding") or {}).get("name") or "",
                "digest": hashlib.sha256(body.encode()).hexdigest(),
                "statement": "위 문서는 승인된 폐기 심의 절차에 따라 복원할 수 없는 방법으로 파기(또는 비식별화)되었음을 확인합니다. 본 확인서의 해시는 폐기 대장 기록으로 다시 계산해 검증할 수 있습니다."}
