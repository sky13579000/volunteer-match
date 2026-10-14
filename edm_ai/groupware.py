"""Groupware -> EDM-AI: register a document that finished approval in the groupware.

The groupware (or an RPA / ESB job) POSTs one signed JSON message per approved form to /api/adapters/groupware:

  {"doc_id": "APPR-2026-00123",                  required - the groupware's own id; makes the call idempotent
   "form": "계약품의", "title": "...",             form -> folder / document type through `routes` in the config
   "owner": {"type": "email", "id": "kim@..."},  required - the drafter; must be mapped in users.json `external`
   "files": [{"filename": "...", "content_b64": "..."}],   >= 1; the others are registered and linked to the first
   "approved": true,                              groupware approval is final -> the version becomes effective at once
   "approval_line": [{"name": "박재정", "title": "팀장", "at": "2026-10-01 10:00", "result": "승인"}],
   "tags": [], "props": {}, "expires_at": "", "level": 0}

The document is registered AS the drafter, so folder permissions, security level and audit apply exactly as for a
person. Resending the same doc_id never creates a duplicate: identical content -> "unchanged", new content -> new version.
"""
from __future__ import annotations

import base64

from .errors import DmsError


def find_folder(dms, path: str):
    """Existing folder by 'A/B/C' path (never auto-created: an unknown path is a configuration error, not a new folder)."""
    parent = None
    for part in [x.strip() for x in path.split("/") if x.strip()]:
        row = dms.q1("SELECT id FROM folders WHERE name=? AND parent_id IS ?", (part, parent))
        if not row:
            return None
        parent = row["id"]
    return parent


def _route(cfg: dict, form: str) -> dict:
    for r in cfg.get("routes", []):
        if r.get("form") == form:
            return r
    d = cfg.get("default_route")
    if not d:
        raise DmsError(422, f"양식 '{form}' 에 대한 routes 설정이 없습니다")
    return d


def _existing(dms, key: str):
    row = dms.q1("SELECT id FROM dms_docs WHERE props LIKE ? AND deleted_at IS NULL", ('%"gw_key": "' + key.replace('"', "") + '"%',))
    if not row:
        return None
    r = dms.row(row["id"])
    return r if r and r["props"].get("gw_key") == key else None


def _line(approval_line) -> str:
    if not isinstance(approval_line, list):
        return ""
    return "; ".join(f"{a.get('name', '?')}({a.get('title', '')}) {a.get('result', '')} {a.get('at', '')}".strip() for a in approval_line[:20] if isinstance(a, dict))


def _get(obj, path: str):
    """'a.b.0.c' -> obj['a']['b'][0]['c'] (None when anything is missing)."""
    for part in str(path).split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def normalize(cfg: dict, body: dict) -> dict:
    """Adapt ANY groupware JSON to the canonical message through `field_map` (no code needed per groupware product).

      "field_map": {"doc_id": "approval.docNo", "form": "approval.formName", "title": "approval.subject",
                    "owner_email": "drafter.email", "owner_id": "drafter.empNo",
                    "approved": {"path": "approval.status", "equals": ["완료", "COMPLETE"]},
                    "approval_line": "approval.line", "tags": "approval.tags", "expires_at": "approval.expireDate",
                    "files": {"path": "attachments", "filename": "fileName", "content_b64": "fileData"}}
    """
    fm = cfg.get("field_map")
    if not fm:
        return body
    out = dict(body)
    for key in ("doc_id", "form", "title", "approval_line", "tags", "expires_at", "description", "level", "props"):
        if key in fm:
            v = _get(body, fm[key])
            if v is not None:
                out[key] = v
    if "owner_email" in fm and _get(body, fm["owner_email"]):
        out["owner"] = {"type": "email", "id": str(_get(body, fm["owner_email"]))}
    elif "owner_id" in fm and _get(body, fm["owner_id"]):
        out["owner"] = {"type": cfg.get("owner_id_type", "empno"), "id": str(_get(body, fm["owner_id"]))}
    if isinstance(fm.get("approved"), dict):
        v = _get(body, fm["approved"].get("path", ""))
        eq = fm["approved"].get("equals", [])
        out["approved"] = v in (eq if isinstance(eq, list) else [eq])
    elif "approved" in fm:
        out["approved"] = bool(_get(body, fm["approved"]))
    if isinstance(fm.get("files"), dict):
        spec = fm["files"]
        items = _get(body, spec.get("path", "")) or []
        out["files"] = [{"filename": _get(i, spec.get("filename", "filename")), "content_b64": _get(i, spec.get("content_b64", "content_b64"))}
                        for i in items if isinstance(i, dict)]
    return out


def ingest(app, cfg: dict, body: dict) -> dict:
    body = normalize(cfg, body)
    d = app.dms
    gw_id = str(body.get("doc_id") or "").strip()
    if not gw_id or len(gw_id) > 100:
        raise DmsError(400, "doc_id 가 필요합니다")
    owner = body.get("owner") or {}
    uid = app.external.get((str(owner.get("type")), str(owner.get("id", "")).lower() if owner.get("type") == "email" else str(owner.get("id", ""))))
    if not uid:
        raise DmsError(403, "기안자가 EDM-AI 사용자와 연결되어 있지 않습니다")
    p = app.users[uid]
    files = body.get("files")
    if not isinstance(files, list) or not files or len(files) > 20:
        raise DmsError(400, "files 는 1~20개여야 합니다")
    route = _route(cfg, str(body.get("form") or ""))
    folder_id = find_folder(d, route.get("folder", ""))
    if folder_id is None:
        raise DmsError(422, f"폴더 '{route.get('folder')}' 가 없습니다 (routes 설정 확인)")
    t = d.doc_type(route.get("doc_type", "GEN"))
    system = str(cfg.get("system") or "gw")[:20]
    approved = bool(body.get("approved")) and bool(cfg.get("trust_approval", True))
    line = _line(body.get("approval_line"))
    tags = list(dict.fromkeys(list(route.get("tags", [])) + list(body.get("tags") or [])))[:20]
    results, first_id = [], None
    with d.batch():
        for i, f in enumerate(files):
            key = f"{system}:{gw_id}" + ("" if i == 0 else f"#{i + 1}")
            item = {"index": i, "filename": (f or {}).get("filename")}
            try:
                data = base64.b64decode(str((f or {}).get("content_b64") or ""), validate=True)
                name = str((f or {}).get("filename") or "")
                row = _existing(d, key)
                props = {**{str(k): v for k, v in (body.get("props") or {}).items()}, "gw_key": key, "gw_system": system, "gw_doc_id": gw_id,
                         "gw_form": str(body.get("form") or "")[:60], "gw_approval": line[:200]}
                if row is None:
                    r = d.register(p, name, data, folder_id=folder_id, title=(body.get("title") if i == 0 else None) or None, doc_type=t["code"], tags=tags,
                                   props=props, description=str(body.get("description") or ""), level=body.get("level", route.get("level", 0)),
                                   expires_at=body.get("expires_at"), comment=f"그룹웨어 {gw_id}")
                    did, status = r["id"], "created"
                    if approved:
                        d._approve_version(did, 1, t, p.id)
                        d.reindex(d.row(did))
                else:
                    did = row["id"]
                    try:
                        d.new_version(p, did, name, data, f"그룹웨어 {gw_id} 재전송")
                        status = "updated"
                        if approved:
                            d._approve_version(did, d.row(did)["current_ver"], t, p.id)
                            d.reindex(d.row(did))
                    except DmsError as e:
                        if e.code != 409 or "내용이 같" not in str(e):
                            raise
                        status = "unchanged"
                if i == 0:
                    first_id = did
                elif first_id and status == "created":
                    d.link_docs(p, first_id, did)
                if line and status == "created":
                    d.add_comment(p, did, "그룹웨어 결재선: " + line)
                item.update(status=status, id=did, doc_no=d.row(did)["doc_no"])
            except DmsError as e:
                item.update(status="error", error=e.msg, code=e.code)
            except Exception:
                item.update(status="error", error="파일 형식이 올바르지 않습니다", code=400)
            results.append(item)
    return {"doc_id": gw_id, "owner": uid, "results": results, "ok": all(r["status"] != "error" for r in results)}
