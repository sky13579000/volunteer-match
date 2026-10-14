from __future__ import annotations

import difflib
import json
import mimetypes
from pathlib import Path

from . import automation
from .dms import ROLE_RANK, add_days, now
from .errors import DmsError
from .safety import redact_pii
from .store import MANAGED_PREFIX


class DocsMixin:
    # ================= folders =================
    def folder_role(self, p, fid) -> int:
        if self.admin(p):
            return 3
        chain = self.folder_chain(fid)
        if any(f["owner"] == p.id for f in chain):
            return 3
        best = 0
        for e in self._folder_entries(fid):
            if e["type"] != "deny" and self._match(p, e):
                best = max(best, ROLE_RANK[e["role"]])
        return best

    def folder_tree(self, p) -> list[dict]:
        counts: dict[int, int] = {}
        for r in self.q("SELECT id FROM dms_docs WHERE deleted_at IS NULL"):
            row = self.row(r["id"])
            if self.can_read(p, row):
                counts[row["folder_id"]] = counts.get(row["folder_id"], 0) + 1
        folders = {f["id"]: f for f in (self.folder(r["id"]) for r in self.q("SELECT id FROM folders"))}
        show = {fid for fid in folders if counts.get(fid) or folders[fid]["open_create"] or self.folder_role(p, fid) >= 1 and not self.admin(p)}
        if self.admin(p):
            show = set(folders)
        for fid in list(show):                                    # ancestors of anything visible
            par = folders[fid]["parent_id"]
            while par is not None and par not in show:
                show.add(par)
                par = folders[par]["parent_id"]
        return [{"id": f["id"], "parent_id": f["parent_id"], "name": f["name"], "origin": f["origin"],
                 "role": self.folder_role(p, f["id"]), "can_register": self.folder_role(p, f["id"]) >= 2 or bool(f["open_create"]) and f["origin"] != "crawled", "doc_count": counts.get(f["id"], 0),
                 **({"entries": f["entries"], "inherit": bool(f["inherit"])} if self.folder_role(p, f["id"]) >= 3 else {})}
                for f in sorted(folders.values(), key=lambda x: (x["parent_id"] or 0, x["name"])) if f["id"] in show]

    @staticmethod
    def _folder_name(name) -> str:
        name = str(name or "").strip()
        if not name or len(name) > 80 or any(c in name for c in '/\\\x00'):
            raise DmsError(400, "폴더 이름이 올바르지 않습니다 (80자 이하, / 사용 불가)")
        return name

    def create_folder(self, p, parent_id, name, entries=None) -> dict:
        name = self._folder_name(name)
        if parent_id is not None:
            par = self.folder(parent_id)
            if not par or (self.folder_role(p, parent_id) < 1 and not self.admin(p)):
                raise DmsError(404, "상위 폴더를 찾을 수 없습니다")
            if par["origin"] == "crawled":
                raise DmsError(409, "외부 소스 폴더에는 폴더를 만들 수 없습니다")
            if self.folder_role(p, parent_id) < 2:
                raise DmsError(403, "상위 폴더에 편집 권한이 필요합니다")
        elif not self.admin(p):
            raise DmsError(403, "최상위 폴더는 관리자만 만들 수 있습니다")
        if self.q1("SELECT 1 AS x FROM folders WHERE name=? AND parent_id IS ?", (name, parent_id)):
            raise DmsError(409, "같은 이름의 폴더가 이미 있습니다")
        cur = self.x("INSERT INTO folders(parent_id,name,owner,created,entries) VALUES(?,?,?,?,?)",
                     (parent_id, name, p.id, now(), json.dumps(self._norm_entries(entries))))
        self.store.log_audit(p.id, f"[폴더 생성] {self.folder_path(cur.lastrowid)}", [], False, "dms")
        return self.folder(cur.lastrowid)

    def rename_folder(self, p, fid, name) -> dict:
        f = self._folder_for_manage(p, fid)
        name = self._folder_name(name)
        if self.q1("SELECT 1 AS x FROM folders WHERE name=? AND parent_id IS ? AND id<>?", (name, f["parent_id"], fid)):
            raise DmsError(409, "같은 이름의 폴더가 이미 있습니다")
        self.x("UPDATE folders SET name=? WHERE id=?", (name, fid))
        self.store.log_audit(p.id, f"[폴더 이름 변경] {f['name']} -> {name}", [], False, "dms")
        return self.folder(fid)

    def _folder_for_manage(self, p, fid) -> dict:
        f = self.folder(fid)
        if not f or (self.folder_role(p, fid) < 1 and not self.admin(p)):
            raise DmsError(404, "폴더를 찾을 수 없습니다")
        if f["origin"] == "crawled" or fid in (1, 2):
            raise DmsError(409, "기본 폴더는 변경할 수 없습니다")
        if self.folder_role(p, fid) < 3:
            raise DmsError(403, "폴더 관리 권한이 필요합니다")
        return f

    def move_folder(self, p, fid, new_parent) -> dict:
        f = self._folder_for_manage(p, fid)
        if new_parent is None:
            if not self.admin(p):
                raise DmsError(403, "최상위로 옮기려면 관리자 권한이 필요합니다")
        else:
            tgt = self.folder(new_parent)
            if not tgt or tgt["origin"] == "crawled":
                raise DmsError(404, "대상 폴더를 찾을 수 없습니다")
            if self.folder_role(p, new_parent) < 2:
                raise DmsError(403, "대상 폴더에 편집 권한이 필요합니다")
            if fid in [c["id"] for c in self.folder_chain(new_parent)]:
                raise DmsError(400, "자기 자신의 하위 폴더로는 옮길 수 없습니다")
        if self.q1("SELECT 1 AS x FROM folders WHERE name=? AND parent_id IS ? AND id<>?", (f["name"], new_parent, fid)):
            raise DmsError(409, "대상에 같은 이름의 폴더가 있습니다")
        self.x("UPDATE folders SET parent_id=? WHERE id=?", (new_parent, fid))
        self.reindex_folder_tree(fid)                         # inherited permissions changed
        self.store.log_audit(p.id, f"[폴더 이동] {f['name']}", [], False, "dms")
        return self.folder(fid)

    def delete_folder(self, p, fid) -> dict:
        f = self._folder_for_manage(p, fid)
        if self.q1("SELECT 1 AS x FROM folders WHERE parent_id=?", (fid,)) or self.q1("SELECT 1 AS x FROM dms_docs WHERE folder_id=?", (fid,)):
            raise DmsError(409, "비어 있는 폴더만 삭제할 수 있습니다 (휴지통 문서 포함)")
        self.x("DELETE FROM folders WHERE id=?", (fid,))
        self.store.log_audit(p.id, f"[폴더 삭제] {f['name']}", [], False, "dms")
        return {"deleted": f["name"]}

    def set_folder_acl(self, p, fid, entries, inherit=None) -> dict:
        f = self._folder_for_manage(p, fid)
        self.x("UPDATE folders SET entries=?, inherit=? WHERE id=?",
               (json.dumps(self._norm_entries(entries)), int(f["inherit"] if inherit is None else bool(inherit)), fid))
        self.reindex_folder_tree(fid)
        self.store.log_audit(p.id, f"[폴더 권한 변경] {self.folder_path(fid)}", [], False, "dms")
        return self.folder(fid)

    # ================= register =================
    def _retention_until(self, t: dict, approved_at: str) -> str | None:
        y = int(t.get("retention_years") or 0)
        return add_days(approved_at, 365 * y + y // 4)[:10] if y > 0 else None

    def _approve_version(self, row_id, ver, t, who):
        """Make `ver` the effective version (previous effective one is superseded)."""
        ts = now()
        old = self.q1("SELECT effective_ver FROM dms_docs WHERE id=?", (row_id,))["effective_ver"]
        with self.db:
            if old and old != ver:
                self.db.execute("UPDATE dms_versions SET status='superseded' WHERE doc_id=? AND ver=?", (row_id, old))
            self.db.execute("UPDATE dms_versions SET status='approved' WHERE doc_id=? AND ver=?", (row_id, ver))
            doc = self.db.execute("SELECT retention_until FROM dms_docs WHERE id=?", (row_id,)).fetchone()
            ret = self._retention_until(t, ts)
            if doc[0] and ret:
                ret = max(doc[0], ret)
            self.db.execute("UPDATE dms_docs SET effective_ver=?, status='approved', approved_at=?, retention_until=COALESCE(?,retention_until), "
                            "updated=? WHERE id=?", (ver, ts, ret, ts, row_id))

    def register(self, p, filename, data: bytes, *, folder_id=1, title=None, doc_type="GEN", tags=None, props=None,
                 description="", level=0, entries=None, inherit=True, expires_at=None, comment="") -> dict:
        name = self.clean_name(filename)
        self.check_size(data)
        f = self.folder(folder_id)
        if f and f["origin"] == "crawled":
            raise DmsError(409, "외부 소스 폴더에는 등록할 수 없습니다")
        if not f or (self.folder_role(p, folder_id) < 1 and not self.admin(p) and not f["open_create"]):
            raise DmsError(404, "폴더를 찾을 수 없습니다")
        if self.folder_role(p, folder_id) < 2 and not f["open_create"]:
            raise DmsError(403, "이 폴더에 문서를 등록할 권한이 없습니다")
        t = self.doc_type(doc_type)
        level = self.clean_level(p, level)
        ents = self._norm_entries(entries)
        tags, props = self.clean_tags(tags), self.clean_props(props)
        expires = self.clean_date(expires_at, "만료일")
        description = str(description or "")[:2000]
        if not ents and not any(e["type"] != "deny" for e in self._folder_entries(folder_id)):
            ents = [{"type": "dept", "id": p.dept, "role": "editor"}] if p.dept else [{"type": "user", "id": p.id, "role": "manager"}]   # safe default
        ts = now()
        doc_no = self.next_no(t)
        cur = self.x("INSERT INTO dms_docs(doc_no,source,title,folder_id,doc_type,owner,created,updated,tags,props,description,level,inherit,entries,expires_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (doc_no, MANAGED_PREFIX + doc_no, "", folder_id, t["code"], p.id, ts, ts, json.dumps(tags), json.dumps(props, ensure_ascii=False),
                      description, level, int(bool(inherit)), json.dumps(ents), expires))
        did = cur.lastrowid
        try:
            rel, sha = self._store_file(did, 1, name, data)
            ttl, text = self._extract(rel, name)
        except Exception:
            self.x("DELETE FROM dms_docs WHERE id=?", (did,))
            import shutil
            shutil.rmtree(self.repo / str(did), ignore_errors=True)
            raise
        self.x("UPDATE dms_docs SET title=?, current_ver=1 WHERE id=?", (str(title or ttl)[:200].strip() or Path(name).stem, did))
        self.x("INSERT INTO dms_versions(doc_id,ver,filename,size,sha256,path,status,created_by,created,comment,text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (did, 1, name, len(data), sha, rel, "draft", p.id, ts, str(comment)[:500], text))
        if not t["approval_required"]:
            self._approve_version(did, 1, t, p.id)
        else:
            self.x("UPDATE dms_docs SET status='draft' WHERE id=?", (did,))
        row = self.row(did)
        self.reindex(row)
        self.log(p, row, "registered", f"{doc_no} v1 {len(data)} bytes")
        out = self.get(p, did)
        out["duplicates"] = self.duplicates(p, sha, exclude=did)
        return out

    def duplicates(self, p, sha: str, exclude=None) -> list[dict]:
        """Identical files the caller may read (hidden duplicates are never revealed)."""
        out = []
        for v in self.q("SELECT DISTINCT doc_id FROM dms_versions WHERE sha256=?", (sha,)):
            if v["doc_id"] == exclude:
                continue
            row = self.row(v["doc_id"])
            if row and not row["deleted_at"] and self.can_read(p, row):
                out.append({"id": row["id"], "doc_no": row["doc_no"], "title": row["title"]})
        return out

    # ================= versions =================
    def _versions(self, doc_id) -> list[dict]:
        return self.q("SELECT id,ver,filename,size,sha256,path,status,created_by,created,comment FROM dms_versions WHERE doc_id=? ORDER BY ver DESC", (doc_id,))

    def visible_vers(self, p, row, pr=None) -> set[int]:
        """Versions the caller may see: editors / approvers see all, others only published ones."""
        pr = pr if pr is not None else self.perms(p, row)
        vs = self._versions(row["id"])
        if "edit" in pr or "approve" in pr or ("manage" in pr and row["owner"] == p.id):
            return {v["ver"] for v in vs}
        return {v["ver"] for v in vs if v["status"] in ("approved", "superseded")}

    def versions(self, p, doc_id) -> list[dict]:
        row = self.get_row(p, doc_id)
        pr = self.perms(p, row)
        vis = self.visible_vers(p, row, pr)
        return [{"ver": v["ver"], "label": f"v{v['ver']}", "filename": v["filename"], "size": v["size"], "status": v["status"],
                 "created_by": v["created_by"], "created": v["created"], "comment": v["comment"],
                 "effective": v["ver"] == row["effective_ver"], "can_download": "download" in pr}
                for v in self._versions(doc_id) if v["ver"] in vis]

    def _ver(self, row, ver) -> dict:
        v = self.q1("SELECT * FROM dms_versions WHERE doc_id=? AND ver=?", (row["id"], int(ver)))
        if not v:
            raise DmsError(404, "버전을 찾을 수 없습니다")
        return v

    def view_ver(self, p, row, pr=None) -> int | None:
        pr = pr if pr is not None else self.perms(p, row)
        vis = self.visible_vers(p, row, pr)
        if ("edit" in pr or "approve" in pr or row["owner"] == p.id) and row["current_ver"] in vis:
            return row["current_ver"]
        return row["effective_ver"] if row["effective_ver"] in vis else None

    def _add_version(self, p, row, name, data, comment, restored_from=None) -> dict:
        t = self.doc_type(row["doc_type"])
        if self.q1("SELECT 1 AS x FROM workflows WHERE doc_id=? AND status='pending'", (row["id"],)):
            raise DmsError(409, "결재가 진행 중인 문서는 새 버전을 올릴 수 없습니다 (결재를 회수하거나 완료하세요)")
        if row["locked_by"] and row["locked_by"] != p.id:
            raise DmsError(409, f"{row['locked_by']} 님이 체크아웃 중입니다")
        self.check_size(data)
        ver = row["current_ver"] + 1
        rel, sha = self._store_file(row["id"], ver, name, data)
        last = self.q1("SELECT sha256 FROM dms_versions WHERE doc_id=? AND ver=?", (row["id"], row["current_ver"]))
        if last and last["sha256"] == sha and not restored_from:
            import shutil
            shutil.rmtree(self.repo / str(row["id"]) / str(ver), ignore_errors=True)
            raise DmsError(409, "이전 버전과 내용이 같습니다")
        try:
            _, text = self._extract(rel, name)
        except Exception:
            import shutil
            shutil.rmtree(self.repo / str(row["id"]) / str(ver), ignore_errors=True)
            raise
        ts = now()
        self.x("INSERT INTO dms_versions(doc_id,ver,filename,size,sha256,path,status,created_by,created,comment,text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (row["id"], ver, name, len(data), sha, rel, "draft", p.id, ts, str(comment)[:500], text))
        lock = None if row["locked_by"] == p.id else row["locked_by"]
        self.x("UPDATE dms_docs SET current_ver=?, status=?, updated=?, locked_by=?, locked_at=? WHERE id=?",
               (ver, "draft", ts, lock, None if lock is None else row["locked_at"], row["id"]))
        if not t["approval_required"]:
            self._approve_version(row["id"], ver, t, p.id)
        row = self.row(row["id"])
        self.reindex(row)
        self.log(p, row, "new_version" if not restored_from else "restored", f"v{ver}" + (f" (v{restored_from} 복원)" if restored_from else ""))
        return self.get(p, row["id"])

    def new_version(self, p, doc_id, filename, data, comment="") -> dict:
        row = self.get_row(p, doc_id, "edit")
        return self._add_version(p, row, self.clean_name(filename), data, comment)

    def restore(self, p, doc_id, ver) -> dict:
        row = self.get_row(p, doc_id, "edit")
        v = self._ver(row, ver)
        if v["ver"] not in self.visible_vers(p, row):
            raise DmsError(404, "버전을 찾을 수 없습니다")
        return self._add_version(p, row, v["filename"], self.read_bytes(v), f"v{v['ver']} 복원", restored_from=v["ver"])

    def checkout(self, p, doc_id) -> dict:
        row = self.get_row(p, doc_id, "edit")
        if row["locked_by"] and row["locked_by"] != p.id:
            raise DmsError(409, f"{row['locked_by']} 님이 이미 체크아웃 중입니다")
        self.x("UPDATE dms_docs SET locked_by=?, locked_at=? WHERE id=?", (p.id, now(), doc_id))
        self.log(p, row, "checked_out")
        return self.get(p, doc_id)

    def cancel_checkout(self, p, doc_id) -> dict:
        row = self.row(doc_id)
        if not row or row["deleted_at"]:
            raise DmsError(404, "문서를 찾을 수 없습니다")
        pr = self.perms(p, row)
        if "read" not in pr and "manage" not in pr:
            raise DmsError(404, "문서를 찾을 수 없습니다")
        if row["locked_by"] and row["locked_by"] != p.id and "manage" not in pr:
            raise DmsError(403, "체크아웃한 사용자 또는 관리자만 해제할 수 있습니다")
        if row["locked_by"]:
            self.x("UPDATE dms_docs SET locked_by=NULL, locked_at=NULL WHERE id=?", (doc_id,))
            self.log(p, row, "checkout_cancelled", f"was {row['locked_by']}")
        return self.get_or_summary(p, doc_id)

    def download(self, p, doc_id, ver=None) -> tuple[str, bytes, str]:
        row = self.get_row(p, doc_id, "download")
        pr = self.perms(p, row)
        if row["origin"] == "crawled":
            path = (self.app._p(self.app.cfg["docs"]["root"]) / row["source"]).resolve()
            if self.app._p(self.app.cfg["docs"]["root"]).resolve() not in path.parents or not path.is_file():
                raise DmsError(404, "원본 파일을 찾을 수 없습니다")
            data, name = path.read_bytes(), path.name
        else:
            ver = int(ver) if ver else self.view_ver(p, row, pr)
            if ver is None or ver not in self.visible_vers(p, row, pr):
                raise DmsError(404, "버전을 찾을 수 없습니다")
            v = self._ver(row, ver)
            data, name = self.read_bytes(v), v["filename"]
        self.log(p, row, "downloaded", name)
        return name, data, mimetypes.guess_type(name)[0] or "application/octet-stream"

    def diff(self, p, doc_id, a, b) -> dict:
        row = self.get_row(p, doc_id)
        vis = self.visible_vers(p, row)
        if int(a) not in vis or int(b) not in vis:
            raise DmsError(404, "버전을 찾을 수 없습니다")
        va, vb = self._ver(row, a), self._ver(row, b)
        la, lb = va["text"].splitlines()[:3000], vb["text"].splitlines()[:3000]
        ops, added, removed = [], 0, 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
            if tag == "equal":
                if i2 - i1 > 6:
                    ops += [("ctx", x) for x in la[i1:i1 + 3]] + [("skip", f"… {i2 - i1 - 6}줄 동일 …")] + [("ctx", x) for x in la[i2 - 3:i2]]
                else:
                    ops += [("ctx", x) for x in la[i1:i2]]
            else:
                ops += [("del", x) for x in la[i1:i2]] + [("add", x) for x in lb[j1:j2]]
                removed += i2 - i1
                added += j2 - j1
        return {"a": int(a), "b": int(b), "added": added, "removed": removed, "same": not (added or removed),
                "lines": [{"tag": t, "text": x[:500]} for t, x in ops[:600]], "truncated": len(ops) > 600,
                "note": "" if va["text"] or vb["text"] else "텍스트를 추출할 수 없는 형식이라 비교할 수 없습니다"}

    # ================= metadata / ACL / move =================
    def update_meta(self, p, doc_id, **f) -> dict:
        row = self.get_row(p, doc_id, "edit")
        sets = {}
        if "title" in f:
            t = str(f["title"] or "").strip()[:200]
            if not t:
                raise DmsError(400, "제목이 비어 있습니다")
            sets["title"] = t
        if "tags" in f:
            sets["tags"] = json.dumps(self.clean_tags(f["tags"]), ensure_ascii=False)
        if "props" in f:
            sets["props"] = json.dumps(self.clean_props(f["props"]), ensure_ascii=False)
        if "description" in f:
            sets["description"] = str(f["description"] or "")[:2000]
        if "expires_at" in f:
            sets["expires_at"] = self.clean_date(f["expires_at"], "만료일")
            sets["expire_notified"] = 0
        if "level" in f:
            if "manage" not in self.perms(p, row) and "acl" not in self.perms(p, row):
                raise DmsError(403, "보안등급은 관리 권한이 필요합니다")
            sets["level"] = self.clean_level(p, f["level"])
        if not sets:
            return self.get(p, doc_id)
        sets["updated"] = now()
        self.x(f"UPDATE dms_docs SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)
        self.log(p, row2, "meta_changed", ", ".join(k for k in sets if k not in ("updated", "expire_notified")))
        return self.get(p, doc_id)

    def set_acl(self, p, doc_id, entries=None, inherit=None, level=None) -> dict:
        row = self.get_row(p, doc_id, "acl")
        if row["origin"] == "crawled":
            return self._set_crawled_acl(p, row, entries, level)
        sets = {}
        if entries is not None:
            sets["entries"] = json.dumps(self._norm_entries(entries))
        if inherit is not None:
            sets["inherit"] = int(bool(inherit))
        if level is not None:
            sets["level"] = self.clean_level(p, level)
        if sets:
            self.x(f"UPDATE dms_docs SET {', '.join(k + '=?' for k in sets)}, updated=? WHERE id=?", (*sets.values(), now(), doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)
        self.log(p, row2, "acl_changed", json.dumps({"entries": row2["entries"], "inherit": row2["inherit"], "level": row2["level"]}, ensure_ascii=False))
        r = self.get_or_summary(p, doc_id)
        return r

    def _set_crawled_acl(self, p, row, entries, level) -> dict:
        """Documents crawled from a folder have a flat ACL (roles/depts/users + level); only admins edit it."""
        if not self.admin(p):
            raise DmsError(403, "수집된 문서의 권한은 관리자만 변경할 수 있습니다")
        ents = self._norm_entries(entries or [])
        cur = self.idx_doc(row)
        acl = Acl_from_entries(ents, cur.acl.level if level is None else self.clean_level(p, level))
        self.store.set_acl_override(row["source"], acl, p.id)
        self.app.ingest()
        self.log(p, row, "acl_changed", "crawled override")
        return self.get_or_summary(p, row["id"])

    def get_or_summary(self, p, doc_id) -> dict:
        row = self.row(doc_id)
        if self.can_read(p, row):
            return self.get(p, doc_id)
        return {**self.summary(p, row), "note": "처리되었습니다. 현재 이 문서를 읽을 권한은 없습니다"}

    def move(self, p, doc_id, folder_id) -> dict:
        row = self.get_row(p, doc_id, "edit")
        f = self.folder(folder_id)
        if not f or f["origin"] == "crawled" or (self.folder_role(p, folder_id) < 1 and not self.admin(p) and not f["open_create"]):
            raise DmsError(404, "대상 폴더를 찾을 수 없습니다")
        if self.folder_role(p, folder_id) < 2 and not f["open_create"]:
            raise DmsError(403, "대상 폴더에 편집 권한이 필요합니다")
        old = self.folder_path(row["folder_id"])
        self.x("UPDATE dms_docs SET folder_id=?, updated=? WHERE id=?", (folder_id, now(), doc_id))
        row2 = self.row(doc_id)
        self.reindex(row2)
        self.log(p, row2, "moved", f"{old} -> {self.folder_path(folder_id)}")
        return self.get_or_summary(p, doc_id)

    # ================= detail =================
    def get(self, p, doc_id) -> dict:
        row = self.row(doc_id)
        if not row or row["deleted_at"]:
            raise DmsError(404, "문서를 찾을 수 없습니다")
        pr = self.perms(p, row)
        if "read" not in pr:
            if "manage" in pr:
                return {**self.summary(p, row), "note": "읽기 권한이 없어 내용은 볼 수 없습니다 (관리만 가능)"}
            raise DmsError(404, "문서를 찾을 수 없습니다")
        d = self.summary(p, row)
        vv = self.view_ver(p, row, pr)
        text = ""
        if row["origin"] == "crawled":
            idx = self.app.engine.index
            doc = self.idx_doc(row)
            text = "\n\n".join(c.text for c in sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord))
        elif vv:
            text = self._ver(row, vv)["text"]
        reveal = bool(self.app.engine.pii_reveal_roles & p.roles)
        d["text"] = text if reveal else redact_pii(text)
        d["viewing_ver"] = vv
        d["props"] = row["props"]
        d["inherit"] = bool(row["inherit"])
        if "acl" in pr or row["origin"] == "crawled" and self.admin(p):
            d["entries"] = row["entries"]
            d["effective_entries"] = self._eff_entries(row)
            ra = self.derive_acl(row) if row["origin"] == "managed" else (self.idx_doc(row).acl if self.idx_doc(row) else None)
            d["read_acl"] = ra.to_dict() if ra else None
        d["versions"] = self.versions(p, doc_id) if row["origin"] == "managed" else []
        d["workflow"] = self.workflow_state(p, row)
        gw = self.app.gateway
        if gw:
            link_types = gw.link_types()
            ents = {e for e in self.app.extractor.extract(f"{row['title']}\n{text}") if e.partition(":")[0] in link_types}
            d["linked_rows"] = [{"table": r["table"], "title": r["title"], "key": r["key"], "masked": list(r["masked"]),
                                 "values": {r["labels"][c]: v for c, v in r["values"].items()}} for r in gw.lookup(p, ents, limit=8)] if ents else []
        else:
            d["linked_rows"] = []
        auto = (self.app.cfg.get("automation") or {}).get("contract_check")
        d["analysis"] = automation.analyze(p, row["title"], text, gw, auto)
        d["history"] = self.store.history(row["source"])
        d["related"] = self.related(p, doc_id)
        d["comment_count"] = self.q1("SELECT COUNT(*) AS n FROM comments WHERE doc_id=? AND deleted=0", (doc_id,))["n"]
        d["favorite"] = bool(self.q1("SELECT 1 AS x FROM favorites WHERE user_id=? AND doc_id=?", (p.id, doc_id)))
        d["legal_hold"] = bool(row["legal_hold"])
        d["props"] = row["props"]
        self.x("INSERT OR REPLACE INTO recent VALUES(?,?,?)", (p.id, doc_id, now()))
        return d


def Acl_from_entries(entries, level):
    from .models import Acl
    return Acl(tuple(e["id"] for e in entries if e["type"] == "role"), tuple(e["id"] for e in entries if e["type"] == "dept"),
               tuple(e["id"] for e in entries if e["type"] == "user"), tuple(e["id"] for e in entries if e["type"] == "deny"), level)
