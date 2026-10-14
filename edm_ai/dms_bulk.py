from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import PurePosixPath

from .dms import now
from .errors import DmsError

MAX_BULK = 200
MAX_ZIP_FILES = 500
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def _csv_cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s       # neutralise spreadsheet formula injection


class BulkMixin:
    def bulk(self, p, action: str, ids: list, params: dict | None = None) -> dict:
        params = params or {}
        if not isinstance(ids, list) or not ids or len(ids) > MAX_BULK:
            raise DmsError(400, f"대상 문서는 1~{MAX_BULK}건이어야 합니다")
        ops = {
            "move": lambda i: self.move(p, i, params.get("folder_id")),
            "delete": lambda i: self.delete(p, i),
            "favorite": lambda i: self.set_favorite(p, i, True),
            "unfavorite": lambda i: self.set_favorite(p, i, False),
            "add_tags": lambda i: self._tag_op(p, i, add=params.get("tags")),
            "remove_tags": lambda i: self._tag_op(p, i, remove=params.get("tags")),
            "set_expiry": lambda i: self.update_meta(p, i, expires_at=params.get("expires_at")),
        }
        if action not in ops:
            raise DmsError(400, f"지원하지 않는 일괄 작업입니다: {action}")
        results, ok = [], 0
        with self.batch():
            for i in ids:
                try:
                    ops[action](int(i))
                    results.append({"id": i, "ok": True})
                    ok += 1
                except DmsError as e:
                    results.append({"id": i, "ok": False, "error": e.msg if e.code != 404 else "찾을 수 없음"})   # hidden == missing
                except (TypeError, ValueError):
                    results.append({"id": i, "ok": False, "error": "잘못된 ID"})
        return {"action": action, "ok": ok, "failed": len(ids) - ok, "results": results}

    def _tag_op(self, p, i, add=None, remove=None):
        row = self.get_row(p, i, "edit")
        tags = list(row["tags"])
        for t in self.clean_tags(add):
            if t not in tags:
                tags.append(t)
        rm = set(self.clean_tags(remove))
        return self.update_meta(p, i, tags=[t for t in tags if t not in rm])

    # ---------- export ----------
    def export_zip(self, p, ids: list) -> tuple[bytes, dict]:
        if not isinstance(ids, list) or not ids or len(ids) > MAX_BULK:
            raise DmsError(400, f"내보낼 문서는 1~{MAX_BULK}건이어야 합니다")
        buf, manifest, skipped, total = io.BytesIO(), [], [], 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in ids:
                try:
                    name, data, _ = self.download(p, int(i))
                    row = self.row(int(i))
                except (DmsError, TypeError, ValueError):
                    skipped.append(f"{i}: 찾을 수 없거나 내보낼 권한이 없습니다")     # same wording for hidden and missing
                    continue
                total += len(data)
                if total > 200 * 1024 * 1024:
                    skipped.append(f"{i}: 전체 크기 제한(200MB) 초과")
                    break
                folder = re.sub(r"[\\:*?\"<>|]", "_", self.folder_path(row["folder_id"]).replace(" / ", "/"))
                z.writestr(f"{folder}/{row['doc_no']}_{name}", data)
                manifest.append([row["doc_no"], row["title"], row["doc_type"], row["status"], row["effective_ver"] or row["current_ver"], row["owner"], folder])
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["doc_no", "title", "type", "status", "version", "owner", "folder"])
            w.writerows([[_csv_cell(c) for c in r] for r in manifest])
            z.writestr("_manifest.csv", "﻿" + out.getvalue())
            if skipped:
                z.writestr("_skipped.txt", "\n".join(skipped))
        return buf.getvalue(), {"exported": len(manifest), "skipped": len(skipped)}

    # ---------- bulk import ----------
    def import_zip(self, p, folder_id, data: bytes, doc_type="GEN", level=0, tags=None) -> dict:
        f = self.folder(folder_id)
        if not f or f["origin"] == "crawled" or (self.folder_role(p, folder_id) < 2 and not f["open_create"]):
            raise DmsError(403, "이 폴더에 등록할 권한이 없습니다")
        try:
            zf = zipfile.ZipFile(__import__("io").BytesIO(data))
        except zipfile.BadZipFile:
            raise DmsError(422, "ZIP 파일을 읽을 수 없습니다")
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ZIP_FILES:
            raise DmsError(413, f"ZIP 안의 파일은 최대 {MAX_ZIP_FILES}개입니다")
        if sum(i.file_size for i in infos) > 500 * 1024 * 1024:
            raise DmsError(413, "압축 해제 후 전체 크기가 너무 큽니다")
        created, skipped = [], []
        with self.batch():
            for info in infos:
                raw = info.filename.replace("\\", "/")
                parts = [x for x in PurePosixPath(raw).parts if x not in ("", ".", "/")]
                if (not parts or ".." in parts or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or "__MACOSX" in parts
                        or parts[-1].lower() in SKIP_NAMES or parts[-1].startswith("._")):
                    if parts and ".." in parts or raw.startswith("/"):
                        skipped.append({"path": raw, "reason": "허용되지 않는 경로"})
                    continue
                if info.file_size > self.max_upload:
                    skipped.append({"path": raw, "reason": "파일이 너무 큽니다"})
                    continue
                try:
                    blob = zf.open(info).read(self.max_upload + 1)         # never trust the declared size
                    if len(blob) > self.max_upload:
                        raise DmsError(413, "파일이 너무 큽니다")
                    fid = folder_id
                    for part in parts[:-1]:
                        fid = self._sub_folder(p, fid, part[:80])
                    d = self.register(p, parts[-1], blob, folder_id=fid, doc_type=doc_type, level=level, tags=tags)
                    created.append({"id": d["id"], "doc_no": d["doc_no"], "title": d["title"], "path": raw})
                except DmsError as e:
                    skipped.append({"path": raw, "reason": e.msg})
        self.store.log_audit(p.id, f"[ZIP 일괄 등록] {len(created)}건", [], False, "dms")
        return {"created": created, "skipped": skipped}

    def _sub_folder(self, p, parent, name) -> int:
        name = self._folder_name(name)
        r = self.q1("SELECT id FROM folders WHERE name=? AND parent_id=?", (name, parent))
        if r:
            return r["id"]
        return self.x("INSERT INTO folders(parent_id,name,owner,created,entries) VALUES(?,?,?,?,'[]')", (parent, name, p.id, now())).lastrowid
