from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .errors import DmsError
from .templates import BUILTIN

EDITABLE = {".md", ".txt"}
MAX_TEXT = 1_000_000
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class EditMixin:
    """Templates (create a document from a form) and in-browser editing of text documents (each save = a new version)."""

    def templates(self, p) -> list[dict]:
        merged = {t["id"]: t for t in BUILTIN}
        for t in self.app.cfg.get("templates", []):
            merged[t["id"]] = {**merged.get(t["id"], {}), **t}
        known = {t["code"] for t in self.list_doc_types(p)}
        return [{"id": t["id"], "name": t["name"], "doc_type": t["doc_type"], "description": t.get("description", ""), "fields": t.get("fields", []), "tags": t.get("tags", [])}
                for t in merged.values() if t["doc_type"] in known]

    def render_template(self, p, template_id, values: dict, title: str = "") -> dict:
        t = next((x for x in {**{b["id"]: b for b in BUILTIN}, **{c["id"]: c for c in self.app.cfg.get("templates", [])}}.values() if x["id"] == template_id), None)
        if not t:
            raise DmsError(404, "양식을 찾을 수 없습니다")
        if not isinstance(values, dict) or len(values) > 40:
            raise DmsError(400, "입력 값 형식이 올바르지 않습니다")
        title = str(title or t["name"]).strip()[:200]
        v = {"date": date.today().isoformat(), "year": str(date.today().year), "author": p.name, "dept": p.dept, "title": title}
        for f in t.get("fields", []):
            v[f["key"]] = str(values.get(f["key"], ""))[:2000]
        text = _VAR.sub(lambda m: v.get(m.group(1), ""), t["body"])
        stem = re.sub(r"[\\/:*?\"<>|\s]+", "_", title).strip("_")[:60] or "문서"
        return {"title": title, "filename": f"{stem}.md", "doc_type": t["doc_type"], "tags": list(t.get("tags", [])), "text": text}

    def _editable_row(self, p, doc_id):
        row = self.get_row(p, doc_id, "edit")
        if row["origin"] != "managed":
            raise DmsError(409, "외부 소스 문서는 편집할 수 없습니다")
        return row

    def read_text(self, p, doc_id) -> dict:
        row = self._editable_row(p, doc_id)
        ver = row["current_ver"]
        v = self._ver(row, ver)
        if Path(v["filename"]).suffix.lower() not in EDITABLE:
            raise DmsError(415, "텍스트(.md/.txt) 문서만 브라우저에서 편집할 수 있습니다")
        if (row["props"] or {}).get("no_download") and not self.admin(p) and row["owner"] != p.id:
            raise DmsError(403, "다운로드가 제한된 문서입니다")
        text = self.read_bytes(v).decode("utf-8", "replace")
        return {"id": row["id"], "doc_no": row["doc_no"], "title": row["title"], "filename": v["filename"], "ver": ver, "text": text,
                "locked_by": row["locked_by"], "status": row["status"]}

    def edit_text(self, p, doc_id, text, comment="") -> dict:
        info = self.read_text(p, doc_id)
        text = str(text or "")
        if not text.strip() or len(text.encode()) > MAX_TEXT:
            raise DmsError(400, "내용은 비어 있지 않고 1MB 이하여야 합니다")
        if text == info["text"]:
            raise DmsError(409, "내용이 바뀌지 않았습니다")
        row = self.row(doc_id)
        return self._add_version(p, row, info["filename"], text.encode(), str(comment or "온라인 편집")[:500])
