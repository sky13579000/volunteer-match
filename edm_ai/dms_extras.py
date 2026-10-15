from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path

from . import automation
from .dms import add_days, now, today
from .errors import DmsError

STOP = set("그리고 그러나 따라서 또는 및 등 위한 대한 있는 있다 없다 한다 하는 에서 으로 에게 이다 합니다 입니다 한다 되어 하여 경우 다음 같은 이상 이하 모든 각각 관련 해당 통해 대해 위해 것이 것은 수는 때에 때는".split())
TYPE_HINT = {"계약서": "CT", "회의록": "MIN", "재무/원가": None, "인사": None, "이메일": None, "시설/배치": None}


class ExtrasMixin:
    """Announcements, duplicate report and upload suggestions."""

    # =============== announcements ===============
    def announcements(self, p) -> list[dict]:
        t = today()
        rows = self.q("SELECT * FROM announcements WHERE (expires IS NULL OR expires>=?) ORDER BY pinned DESC, id DESC LIMIT 20", (t,))
        for r in rows:
            r["pinned"] = bool(r["pinned"])
        return rows

    def save_announcement(self, p, spec: dict) -> dict:
        self._need_admin(p)
        title, body = str(spec.get("title") or "").strip()[:120], str(spec.get("body") or "").strip()[:2000]
        if not title:
            raise DmsError(400, "제목을 입력하세요")
        exp = self.clean_date(spec.get("expires"), "게시 종료일")
        cur = self.x("INSERT INTO announcements(title,body,pinned,created_by,created,expires) VALUES(?,?,?,?,?,?)", (title, body, int(bool(spec.get("pinned"))), p.id, now(), exp))
        for uid in self.app.users:
            if uid != p.id:
                self.notify(uid, "notice", None, f"공지: {title}")
        return {"id": cur.lastrowid}

    def delete_announcement(self, p, aid) -> dict:
        self._need_admin(p)
        self.x("DELETE FROM announcements WHERE id=?", (int(aid),))
        return {"deleted": int(aid)}

    # =============== duplicate documents ===============
    def duplicate_report(self, p) -> dict:
        """Documents whose current file is byte-identical. Titles are shown only for documents the viewer may read."""
        if not (self.admin(p) or "records" in p.roles):
            raise DmsError(403, "관리자 또는 기록물 담당자만 볼 수 있습니다")
        groups = []
        waste = 0
        for g in self.q("SELECT v.sha256 AS sha, COUNT(*) AS n, MAX(v.size) AS size FROM dms_versions v JOIN dms_docs d ON d.id=v.doc_id AND d.current_ver=v.ver "
                        "WHERE d.deleted_at IS NULL GROUP BY v.sha256 HAVING COUNT(*)>1 ORDER BY COUNT(*)*MAX(v.size) DESC LIMIT 100"):
            items = []
            for r in self.q("SELECT d.id FROM dms_versions v JOIN dms_docs d ON d.id=v.doc_id AND d.current_ver=v.ver WHERE v.sha256=? AND d.deleted_at IS NULL", (g["sha"],)):
                row = self.row(r["id"])
                items.append({"doc_id": row["id"], "doc_no": row["doc_no"], "title": row["title"] if self.can_read(p, row) else None, "owner": row["owner"],
                              "folder": self.folder_path(row["folder_id"]) if row["folder_id"] else "", "created": row["created"][:10]})
            waste += g["size"] * (g["n"] - 1)
            groups.append({"sha256": g["sha"], "count": g["n"], "size": g["size"], "items": items})
        return {"groups": groups, "wasted_bytes": waste, "note": "내용이 완전히 같은 파일입니다. 소유자와 확인한 뒤 하나만 남기고 정리하세요(자동 삭제하지 않습니다)."}

    # =============== upload suggestions ===============
    def suggest(self, p, filename: str, data: bytes) -> dict:
        name = self.clean_name(filename)
        self.check_size(data)
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / ("f" + Path(name).suffix.lower())
            f.write_bytes(data)
            try:
                from .ingest import SUPPORTED, read_file
                title, text = read_file(f) if f.suffix in SUPPORTED else (Path(name).stem, "")
            except Exception:
                title, text = Path(name).stem, ""
        cat = automation.classify(title, text)
        strip = lambda w: w[:-1] if len(w) >= 3 and w[-1] in "은는이가을를의에도와과로" else w  # noqa: E731   (drop a trailing particle)
        words = Counter(w for w in (strip(x) for x in re.findall(r"[가-힣A-Za-z]{2,12}", text)) if len(w) >= 2 and w not in STOP)
        tags = [w for w, n in words.most_common(12) if n >= 2][:5]
        types = {t["code"] for t in self.q("SELECT code FROM doc_types WHERE active=1")}
        dt = TYPE_HINT.get(cat)
        cls = None
        low = (title + "\n" + text[:3000])
        for c in self.q("SELECT code,name FROM classifications WHERE active=1 AND (retention_years>0 OR parent IS NOT NULL AND parent<>'') ORDER BY length(name) DESC"):
            if len(c["name"]) >= 3 and c["name"].split("·")[0] in low:
                cls = c
                break
        return {"title": title[:120], "category": cat, "doc_type": dt if dt in types else None, "tags": tags, "class": {"code": cls["code"], "name": cls["name"]} if cls else None,
                "summary": automation.summarize(text, 2) if text else "", "note": "규칙 기반 제안입니다. 확인 후 적용하세요."}
