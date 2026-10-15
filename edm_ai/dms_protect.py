from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .dms import now
from .errors import DmsError

TEXT_EXT = (".md", ".txt", ".csv")


class ProtectMixin:
    """Download-time protection: visible watermark for text files and an optional hand-off to an external DRM / packaging service."""

    def _protect_cfg(self) -> dict:
        return {"watermark": False, "drm": None, **(self.app.cfg.get("protection") or {})}

    def protect_download(self, p, row, name: str, data: bytes) -> tuple[str, bytes]:
        cfg = self._protect_cfg()
        uid = p if isinstance(p, str) else p.id
        if cfg["watermark"] and name.lower().endswith(TEXT_EXT):
            mark = f"\n\n---\n[ICC JEJU] {row['doc_no']} · 다운로드 {uid} · {now()} · 무단 배포 금지\n"
            data = data + mark.encode()
        drm = cfg.get("drm")
        if drm and drm.get("url") and row["level"] >= int(drm.get("min_level", 0)):
            data, name = self._drm_package(drm, row, uid, name, data)
        return name, data

    def _drm_package(self, drm: dict, row, uid: str, name: str, data: bytes) -> tuple[bytes, str]:
        """POST the file to the organisation's DRM/packaging server (e.g. Fasoo/Markany gateway). Fail closed: no packaged copy, no download."""
        secret = os.environ.get(drm.get("secret_env", ""), "")
        ts = str(int(__import__("time").time()))
        req = urllib.request.Request(drm["url"], data, {"Content-Type": "application/octet-stream", "X-EDM-Doc": row["doc_no"], "X-EDM-User": uid, "X-EDM-Level": str(row["level"]),
                                                        "X-EDM-Filename": urllib.parse.quote(name), "X-EDM-Time": ts,
                                                        "X-EDM-Signature": hmac.new(secret.encode(), ts.encode() + b"." + hashlib.sha256(data).digest(), "sha256").hexdigest() if secret else ""}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=float(drm.get("timeout", 20))) as r:
                out = r.read(200 * 1024 * 1024)
                ext = r.headers.get("X-EDM-Filename")
                return out, urllib.parse.unquote(ext) if ext else name
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            self.store.log_audit(uid, f"[DRM 패키징 실패] {row['doc_no']}: {type(e).__name__}", [row["source"]], False, "dms")
            raise DmsError(502, "문서 보호(DRM) 서버에 연결할 수 없어 다운로드를 차단했습니다")
