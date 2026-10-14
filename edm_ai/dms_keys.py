from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .dms import now
from .errors import DmsError

PREFIX = "edmk_"
SCOPES = {"read": "조회 전용 (GET)", "write": "조회 + 등록·수정"}


class KeysMixin:
    """API keys for integrations (groupware, RPA, batch jobs). A key acts as ONE existing user, inherits exactly that user's
    permissions, can be limited to read-only, expires, can be revoked, and can never reach the administration API - even when
    the user it acts as is an administrator. Only a SHA-256 hash is stored; the key itself is shown once, when it is created."""

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def create_api_key(self, p, name, user_id, scope="read", days=90) -> dict:
        self._need_admin(p)
        name = str(name or "").strip()[:60]
        if not name:
            raise DmsError(400, "키 이름을 입력하세요")
        if user_id not in self.app.users:
            raise DmsError(400, "알 수 없는 사용자입니다")
        if scope not in SCOPES:
            raise DmsError(400, "범위는 read 또는 write 입니다")
        try:
            days = max(1, min(int(days), 365))
        except (TypeError, ValueError):
            raise DmsError(400, "유효기간은 일 수(1~365)입니다")
        raw = f"{PREFIX}{secrets.token_urlsafe(9)}_{secrets.token_urlsafe(32)}"
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.x("INSERT INTO api_keys(name,user_id,key_hash,prefix,scope,created_by,created,expires) VALUES(?,?,?,?,?,?,?,?)",
                     (name, user_id, self._hash(raw), raw[:14], scope, p.id, now(), expires))
        self.store.log_audit(p.id, f"[API 키 발급] #{cur.lastrowid} {name} → {user_id} ({scope}, {days}일)", [], False, "dms")
        return {"id": cur.lastrowid, "key": raw, "prefix": raw[:14], "expires": expires, "scope": scope, "user": user_id,
                "note": "이 키는 지금 한 번만 표시됩니다. 안전한 곳에 보관하세요."}

    def list_api_keys(self, p) -> list[dict]:
        self._need_admin(p)
        t = now()
        return [{**{k: r[k] for k in ("id", "name", "user_id", "prefix", "scope", "created_by", "created", "expires", "last_used")},
                 "status": "revoked" if r["revoked"] else "expired" if r["expires"] < t else "active"} for r in self.q("SELECT * FROM api_keys ORDER BY id DESC LIMIT 200")]

    def revoke_api_key(self, p, key_id) -> dict:
        self._need_admin(p)
        if not self.x("UPDATE api_keys SET revoked=1 WHERE id=? AND revoked=0", (int(key_id),)).rowcount:
            raise DmsError(404, "키를 찾을 수 없습니다")
        self.store.log_audit(p.id, f"[API 키 폐기] #{key_id}", [], False, "dms")
        return {"revoked": int(key_id)}

    def resolve_api_key(self, raw: str):
        """(principal, scope, key id) for a valid key, else None. Constant-work lookup by hash; last_used updated at most once a minute."""
        if not raw.startswith(PREFIX) or len(raw) > 200:
            return None
        r = self.q1("SELECT * FROM api_keys WHERE key_hash=?", (self._hash(raw),))
        if not r or r["revoked"] or r["expires"] < now():
            return None
        u = self.app.users.get(r["user_id"])
        if not u:
            return None
        if not r["last_used"] or r["last_used"][:16] != now()[:16]:
            self.x("UPDATE api_keys SET last_used=? WHERE id=?", (now(), r["id"]))
        return u, r["scope"], r["id"]
