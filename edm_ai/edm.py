from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import automation
from .ingest import SUPPORTED
from .models import Acl
from .safety import redact_pii

MAX_UPLOAD = 10 * 1024 * 1024


class EdmError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Edm:
    """Document-management operations. Every call takes the authenticated Principal and re-checks
    permissions server-side; a document the caller may not read is reported as 404 (existence is not leaked)."""

    def __init__(self, app):
        self.app = app

    # ---- helpers ----
    @property
    def _root(self) -> Path:
        return self.app._p(self.app.cfg["docs"]["root"])

    @property
    def _auto_cfg(self):
        return (self.app.cfg.get("automation") or {}).get("contract_check")

    @staticmethod
    def _admin(p) -> bool:
        return "admin" in p.roles

    def _visible(self, p, doc_id: int, manage: bool = False):
        """manage=True: admins may administer (ACL / delete) a document they cannot read. They still cannot read it."""
        doc = self.app.engine.index.docs.get(doc_id)
        if not doc or not (doc.acl.allows(p) or (manage and self._admin(p))):
            raise EdmError(404, "문서를 찾을 수 없습니다")
        return doc

    def _can_edit(self, p, source: str) -> bool:
        return self._admin(p) or self.app.store.get_meta(source)["owner"] == p.id

    def _parse_acl(self, p, raw: dict | None) -> Acl:
        if raw is None:
            raw = {"depts": [p.dept], "level": p.level} if p.dept else {"users": [p.id], "level": p.level}
        try:
            lists = {k: [str(x)[:64] for x in (raw.get(k) or [])][:50] for k in ("roles", "depts", "users", "deny_users")}
            acl = Acl(tuple(lists["roles"]), tuple(lists["depts"]), tuple(lists["users"]), tuple(lists["deny_users"]),
                      int(raw.get("level", 0)))
        except (TypeError, ValueError, AttributeError):
            raise EdmError(400, "권한(ACL) 형식이 올바르지 않습니다")
        if not 0 <= acl.level <= 99:
            raise EdmError(400, "보안등급은 0~99 입니다")
        if not self._admin(p):
            if acl.level > p.level:
                raise EdmError(400, "본인 보안등급보다 높은 등급은 지정할 수 없습니다")
            if not acl.allows(p):
                raise EdmError(400, "본인이 읽을 수 없는 권한은 설정할 수 없습니다 (자기 자신을 제외하지 마세요)")
        return acl

    def _summary(self, p, doc) -> dict:
        idx = self.app.engine.index
        chunks = [c for c in idx.chunks if c.doc_id == doc.id]
        meta = self.app.store.get_meta(doc.source)
        can_edit = self._can_edit(p, doc.source)
        readable = doc.acl.allows(p)
        out = {"id": doc.id, "source": doc.source, "title": doc.title, "readable": readable, "chunks": len(chunks),
               "entities": sorted({e for c in chunks for e in c.entities}) if readable else [], "owner": meta["owner"],
               "managed": meta["managed"], "created": meta["created"], "can_edit": can_edit}
        if can_edit:
            out["acl"] = doc.acl.to_dict()
        return out

    # ---- queries ----
    def list(self, p, q: str | None = None) -> list[dict]:
        idx = self.app.engine.index
        visible = [d for d in idx.docs.values() if d.acl.allows(p) or (self._admin(p) and not q)]
        if q:
            ents = self.app.extractor.extract(q)
            hits = idx.search(p, q, ents, k=100, per_doc=1)
            order = {idx.chunks.index(c): r for r, (c, _) in enumerate(hits)}
            ranked = [c.doc_id for c, _ in hits]
            ql = q.lower()
            ranked += [d.id for d in visible if ql in d.title.lower() or ql in d.source.lower()]
            seen, ids = set(), []
            for i in ranked:
                if i not in seen:
                    seen.add(i); ids.append(i)
            visible = [idx.docs[i] for i in ids if idx.docs[i].acl.allows(p)]
        else:
            visible.sort(key=lambda d: d.source)
        return [self._summary(p, d) for d in visible]

    def get(self, p, doc_id: int) -> dict:
        doc = self._visible(p, doc_id)
        idx = self.app.engine.index
        chunks = sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord)
        reveal = bool(self.app.engine.pii_reveal_roles & p.roles)
        text = "\n\n".join(c.text for c in chunks)
        gw = self.app.gateway
        link_types = gw.link_types() if gw else set()
        ents = {e for c in chunks for e in c.entities if e.partition(":")[0] in link_types}
        rows = []
        if gw and ents:
            for r in gw.lookup(p, ents, limit=8):
                rows.append({"table": r["table"], "title": r["title"], "key": r["key"], "masked": list(r["masked"]),
                             "values": {r["labels"][c]: v for c, v in r["values"].items()}})
        d = self._summary(p, doc)
        d.update(text=text if reveal else redact_pii(text), linked_rows=rows,
                 analysis=automation.analyze(p, doc.title, text, gw, self._auto_cfg),
                 history=self.app.store.history(doc.source))
        return d

    def alerts(self, p) -> list[dict]:
        out = []
        idx = self.app.engine.index
        for doc in sorted(idx.docs.values(), key=lambda d: d.source):
            if not doc.acl.allows(p):
                continue
            text = "\n\n".join(c.text for c in sorted((c for c in idx.chunks if c.doc_id == doc.id), key=lambda c: c.ord))
            a = automation.analyze(p, doc.title, text, self.app.gateway, self._auto_cfg)
            if a["findings"]:
                out.append({"doc_id": doc.id, "source": doc.source, "title": doc.title, "category": a["category"],
                            "findings": a["findings"]})
        return out

    # ---- mutations ----
    def upload(self, p, filename: str, data: bytes, acl_raw: dict | None) -> dict:
        name = re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "_", Path(str(filename)).name).strip()
        if not name or name.startswith(".") or len(name) > 150:
            raise EdmError(400, "파일 이름이 올바르지 않습니다")
        if Path(name).suffix.lower() not in SUPPORTED:
            raise EdmError(400, "지원하지 않는 형식입니다: " + ", ".join(sorted(SUPPORTED)))
        if not data or len(data) > MAX_UPLOAD:
            raise EdmError(413 if data else 400, f"파일은 1바이트 이상 {MAX_UPLOAD // 1048576}MB 이하여야 합니다")
        acl = self._parse_acl(p, acl_raw)
        source = f"uploads/{name}"
        meta = self.app.store.get_meta(source)
        if (self._root / source).exists() and not (meta["managed"] and self._can_edit(p, source)):
            raise EdmError(409, "같은 이름의 문서가 이미 있고 수정 권한이 없습니다")
        dest = (self._root / source).resolve()
        if self._root.resolve() not in dest.parents:
            raise EdmError(400, "파일 경로가 올바르지 않습니다")
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        self.app.store.set_meta(source, meta["owner"] or p.id, True)
        self.app.store.set_acl_override(source, acl, p.id)
        stats = self.app.ingest()
        doc = next((d for d in self.app.engine.index.docs.values() if d.source == source), None)
        if doc is None:   # parser refused it: do not keep an unindexed file around
            dest.unlink(missing_ok=True)
            self.app.store.forget(source)
            raise EdmError(422, "문서를 읽을 수 없습니다: " + "; ".join(x for x in stats["skipped"] if source in x) or "알 수 없는 오류")
        self.app.store.add_history(source, p.id, "uploaded", f"{len(data)} bytes")
        self.app.store.log_audit(p.id, f"[문서 업로드] {source}", [source], False, "edm")
        return self.get(p, doc.id) if doc.acl.allows(p) else self._summary(p, doc)

    def set_acl(self, p, doc_id: int, acl_raw: dict) -> dict:
        doc = self._visible(p, doc_id, manage=True)
        if not self._can_edit(p, doc.source):
            raise EdmError(403, "소유자 또는 관리자만 권한을 변경할 수 있습니다")
        acl = self._parse_acl(p, acl_raw or {})
        self.app.store.set_acl_override(doc.source, acl, p.id)
        self.app.store.add_history(doc.source, p.id, "acl_changed", str(acl.to_dict()))
        self.app.store.log_audit(p.id, f"[권한 변경] {doc.source}", [doc.source], False, "edm")
        self.app.ingest()
        new = next((d for d in self.app.engine.index.docs.values() if d.source == doc.source), None)
        if new is None:
            raise EdmError(500, "권한 변경 후 문서를 찾을 수 없습니다")
        if not new.acl.allows(p):
            return {**self._summary(p, new), "note": "변경 후 본인은 이 문서를 읽을 수 없고 관리만 할 수 있습니다"}
        return self.get(p, new.id)

    def delete(self, p, doc_id: int) -> dict:
        doc = self._visible(p, doc_id, manage=True)
        if not self._can_edit(p, doc.source):
            raise EdmError(403, "소유자 또는 관리자만 삭제할 수 있습니다")
        if not self.app.store.get_meta(doc.source)["managed"]:
            raise EdmError(409, "원본 폴더에서 수집된 문서는 여기서 삭제할 수 없습니다 (원본을 지우면 다음 수집 때 제거됩니다)")
        (self._root / doc.source).unlink(missing_ok=True)
        self.app.store.add_history(doc.source, p.id, "deleted")
        self.app.store.log_audit(p.id, f"[문서 삭제] {doc.source}", [doc.source], False, "edm")
        self.app.store.forget(doc.source)
        self.app.engine.reload()
        return {"deleted": doc.source}
