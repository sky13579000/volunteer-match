"""License file: {"payload": {...}, "signature": "<base64 Ed25519>"} signed by the vendor.

payload: customer, edition, max_users, issued, expires (YYYY-MM-DD), features[]. The public key is read from config `license.public_key` /
env EDM_AI_LICENSE_PUBKEY (hex) or the VENDOR_PUBKEY constant below (set it when you build the release). The vendor's PRIVATE key is never in this repository.
Enforcement is intentionally soft - the platform keeps running and shows a banner - because a pure-Python program cannot technically prevent
tampering; the signature proves authenticity of the terms, and the contract does the rest."""
from __future__ import annotations

import base64
import json
import os
from datetime import date
from pathlib import Path

from . import ed25519

VENDOR_PUBKEY = ""          # 64 hex chars; fill in when building a release (tools/license_tool.py keygen)


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class License:
    def __init__(self, app):
        self.app = app
        c = app.cfg.get("license") or {}
        self.path = app._p(c.get("path", "license.json"))
        self.pub_hex = (os.environ.get("EDM_AI_LICENSE_PUBKEY") or c.get("public_key") or VENDOR_PUBKEY).strip()

    def _load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def check(self, doc: dict | None) -> dict:
        if not doc:
            return {"mode": "evaluation", "message": "평가판입니다. 정식 라이선스를 등록하세요."}
        if not self.pub_hex:
            return {"mode": "invalid", "message": "라이선스 검증 공개키가 설정되지 않았습니다 (license.public_key)."}
        try:
            payload, sig = doc["payload"], base64.b64decode(doc["signature"])
            ok = ed25519.verify(bytes.fromhex(self.pub_hex), canonical(payload), sig)
        except (KeyError, ValueError, TypeError):
            ok = False
        if not ok:
            return {"mode": "invalid", "message": "라이선스 서명이 올바르지 않습니다."}
        try:
            left = (date.fromisoformat(payload["expires"]) - date.today()).days
        except (KeyError, ValueError):
            return {"mode": "invalid", "message": "라이선스 만료일 형식이 올바르지 않습니다."}
        users = len(self.app.users)
        mx = int(payload.get("max_users") or 0)
        mode = "expired" if left < 0 else "licensed"
        msg = "라이선스가 만료되었습니다. 갱신하세요." if left < 0 else (f"라이선스 만료 {left}일 전입니다." if left <= 30 else "")
        if mx and users > mx:
            msg = (msg + " " if msg else "") + f"사용자 수({users})가 계약 한도({mx})를 넘었습니다."
        return {"mode": mode, "customer": payload.get("customer", ""), "edition": payload.get("edition", ""), "expires": payload["expires"], "days_left": left,
                "max_users": mx, "users": users, "features": payload.get("features", []), "message": msg}

    def status(self) -> dict:
        return self.check(self._load())

    def install(self, text: str) -> dict:
        try:
            doc = json.loads(text)
        except ValueError:
            raise ValueError("라이선스 파일 형식이 올바르지 않습니다 (JSON)")
        st = self.check(doc)
        if st["mode"] in ("invalid", "evaluation"):
            raise ValueError(st["message"])
        tmp = self.path.with_suffix(".part")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)
        return st
