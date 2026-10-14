from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from .dms import add_days, now
from .errors import DmsError
from .safety import redact_pii

GENERIC = "링크가 올바르지 않거나 만료되었습니다"
MAX_FAILS, LOCK_SECONDS = 5, 900
ITER = 200_000


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pw(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITER).hex()


class ShareMixin:
    """External share links: the one deliberate bypass of the ACL, so it is narrow. Only managers of a published,
    low-classification document; random token stored only as a hash; expiry mandatory; optional password with lockout;
    pinned to the effective version (a superseded / expired / deleted document stops being served); every access audited."""

    def create_share(self, p, doc_id, days=7, max_downloads=0, password=None, perm="view", note="") -> dict:
        if not self.share_cfg["enabled"]:
            raise DmsError(403, "외부 공유가 비활성화되어 있습니다 (설정 dms.external_share.enabled)")
        row = self.get_row(p, doc_id, "share")
        if row["origin"] != "managed" or row["effective_ver"] is None or row["status"] == "expired":
            raise DmsError(409, "승인(게시)된 문서만 외부 공유할 수 있습니다")
        if row["level"] > int(self.share_cfg["max_level"]):
            raise DmsError(403, f"보안등급 {self.share_cfg['max_level']} 초과 문서는 외부 공유할 수 없습니다")
        if perm not in ("view", "download"):
            raise DmsError(400, "공유 권한은 view/download 입니다")
        if perm == "download" and row["props"].get("no_download"):
            raise DmsError(403, "다운로드가 제한된 문서입니다")
        try:
            days, maxdl = int(days), int(max_downloads or 0)
        except (TypeError, ValueError):
            raise DmsError(400, "유효기간/다운로드 횟수가 올바르지 않습니다")
        if not 1 <= days <= int(self.share_cfg["max_days"]) or not 0 <= maxdl <= 1000:
            raise DmsError(400, f"유효기간은 1~{self.share_cfg['max_days']}일, 다운로드 횟수는 0~1000 입니다")
        salt = pwh = None
        if password:
            if len(str(password)) < 6:
                raise DmsError(400, "비밀번호는 6자 이상이어야 합니다")
            salt = secrets.token_bytes(16)
            pwh = _pw(str(password), salt)
        token = secrets.token_urlsafe(32)
        cur = self.x("INSERT INTO shares(doc_id,ver,token_hash,created_by,created,expires,max_dl,pw_salt,pw_hash,perm,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (doc_id, row["effective_ver"], _h(token), p.id, now(), add_days(now(), days), maxdl,
                      salt.hex() if salt else None, pwh, perm, str(note)[:200]))
        self.log(p, row, "share_created", f"#{cur.lastrowid} {perm} {days}d" + (" pw" if pwh else ""))
        return {"id": cur.lastrowid, "token": token, "path": f"/s/{token}", "expires": add_days(now(), days), "perm": perm,
                "password": bool(pwh), "note": "이 링크는 지금만 표시됩니다. 서버에는 해시만 저장됩니다."}

    def list_shares(self, p, doc_id) -> list[dict]:
        self.get_row(p, doc_id, "share")
        t = now()
        return [{"id": s["id"], "created_by": s["created_by"], "created": s["created"], "expires": s["expires"], "perm": s["perm"],
                 "max_downloads": s["max_dl"], "downloads": s["dl_count"], "views": s["views"], "revoked": bool(s["revoked"]),
                 "expired": s["expires"] < t, "has_password": bool(s["pw_hash"]), "note": s["note"], "ver": s["ver"]}
                for s in self.q("SELECT * FROM shares WHERE doc_id=? ORDER BY id DESC", (doc_id,))]

    def revoke_share(self, p, doc_id, share_id) -> dict:
        row = self.get_row(p, doc_id, "share")
        s = self.q1("SELECT * FROM shares WHERE id=? AND doc_id=?", (share_id, doc_id))
        if not s:
            raise DmsError(404, "공유를 찾을 수 없습니다")
        self.x("UPDATE shares SET revoked=1 WHERE id=?", (share_id,))
        self.log(p, row, "share_revoked", f"#{share_id}")
        return {"revoked": share_id}

    # ---------------- public side (no login) ----------------
    def _share(self, token: str) -> tuple[dict, dict]:
        s = self.q1("SELECT * FROM shares WHERE token_hash=?", (_h(str(token)[:200]),))
        if not s or s["revoked"] or s["expires"] < now():
            raise DmsError(404, GENERIC)
        row = self.row(s["doc_id"])
        if not row or row["deleted_at"] or row["status"] == "expired" or row["effective_ver"] != s["ver"] or row["legal_hold"] and False:
            raise DmsError(404, GENERIC)                       # same message for every failure: no oracle
        return s, row

    def share_info(self, token: str) -> dict:
        s, row = self._share(token)
        if s["pw_hash"]:
            return {"requires_password": True}
        return self._share_view(s, row, anonymous=True)

    def _share_view(self, s, row, anonymous=False) -> dict:
        v = self._ver(row, s["ver"])
        self.x("UPDATE shares SET views=views+1 WHERE id=?", (s["id"],))
        self.store.log_audit(f"share:{s['id']}", f"[외부 공유 열람] {row['doc_no']}", [row["source"]], False, "dms")
        self.store.add_history(row["source"], f"share:{s['id']}", "share_viewed", "")
        return {"requires_password": False, "title": row["title"], "doc_no": row["doc_no"], "filename": v["filename"], "size": v["size"],
                "perm": s["perm"], "expires": s["expires"], "text": redact_pii(v["text"]) if s["perm"] in ("view", "download") else "",
                "downloads_left": None if not s["max_dl"] else max(0, s["max_dl"] - s["dl_count"])}

    def _check_password(self, s, password):
        if s["locked_until"] and s["locked_until"] > time.time():
            raise DmsError(429, "시도 횟수를 초과했습니다. 잠시 후 다시 시도하세요")
        if s["pw_hash"]:
            ok = bool(password) and hmac.compare_digest(_pw(str(password), bytes.fromhex(s["pw_salt"])), s["pw_hash"])
            if not ok:
                fails = s["fails"] + 1
                self.x("UPDATE shares SET fails=?, locked_until=? WHERE id=?", (0 if fails >= MAX_FAILS else fails,
                                                                               time.time() + LOCK_SECONDS if fails >= MAX_FAILS else 0, s["id"]))
                self.store.log_audit(f"share:{s['id']}", "[외부 공유 비밀번호 실패]", [], False, "dms")
                raise DmsError(403, "비밀번호가 올바르지 않습니다")
            self.x("UPDATE shares SET fails=0 WHERE id=?", (s["id"],))

    def share_access(self, token: str, password=None) -> dict:
        s, row = self._share(token)
        self._check_password(s, password)
        return self._share_view(s, row)

    def share_download(self, token: str, password=None) -> tuple[str, bytes, str]:
        s, row = self._share(token)
        self._check_password(s, password)
        if s["perm"] != "download":
            raise DmsError(403, "이 링크는 다운로드가 허용되지 않습니다")
        if s["max_dl"] and s["dl_count"] >= s["max_dl"]:
            raise DmsError(403, "다운로드 횟수를 모두 사용했습니다")
        v = self._ver(row, s["ver"])
        data = self.read_bytes(v)
        self.x("UPDATE shares SET dl_count=dl_count+1 WHERE id=?", (s["id"],))
        self.store.log_audit(f"share:{s['id']}", f"[외부 공유 다운로드] {row['doc_no']}", [row["source"]], False, "dms")
        self.store.add_history(row["source"], f"share:{s['id']}", "share_downloaded", v["filename"])
        import mimetypes
        return v["filename"], data, mimetypes.guess_type(v["filename"])[0] or "application/octet-stream"
