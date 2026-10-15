"""Connections managed from the admin UI (NAS / FTP / WebDAV sources and IMAP mailboxes), stored in connections.json next to config.json.

They are merged into the running configuration, so config.json entries and UI entries behave the same. Secrets: a password typed in the UI is
stored in connections.json (file mode 600, never returned by the API, never written to logs/audit). For stricter setups use user_env /
password_env (environment variables) in config.json instead - see docs/SECURITY.md."""
from __future__ import annotations

import imaplib
import json
import os
import ssl
import threading
from pathlib import Path

from .errors import DmsError
from .sources import NAME_RE, SourceError, SourceManager, _dav_request, _ftp_session

SECRET = ("password",)
SRC_KEYS = ("name", "type", "path", "host", "port", "tls", "url", "user", "password", "mode", "include", "exclude", "max_mb", "acl", "schedule_minutes",
            "folder", "owner", "doc_type", "on_delete")
MAIL_KEYS = ("name", "host", "port", "ssl", "user", "password", "mailbox", "register", "folder", "doc_type", "unknown_sender", "fallback_owner",
             "allowed_senders", "max_mb", "move_to", "schedule_minutes", "level", "rules")


class Connections:
    def __init__(self, app):
        self.app = app
        self.path = app._p(app.cfg.get("connections_file", "connections.json"))
        self._lock = threading.Lock()

    # ---------------- storage ----------------
    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            d = {}
        return {"sources": list(d.get("sources", [])), "mail": list(d.get("mail", []))}

    def _write(self, d: dict):
        tmp = self.path.with_suffix(".part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def merge_into(self, cfg: dict):
        """Called at startup (before the managers are built) and after every change."""
        d = self._read()
        base_src = [c for c in cfg.get("sources", []) if not c.get("_ui")]
        names = {c["name"] for c in base_src}
        cfg["sources"] = base_src + [{**c, "_ui": True} for c in d["sources"] if c["name"] not in names]
        mail = cfg.setdefault("mail", {})
        base_mail = [c for c in mail.get("imap", []) if not c.get("_ui")]
        mnames = {c["name"] for c in base_mail}
        mail["imap"] = base_mail + [{**c, "_ui": True} for c in d["mail"] if c["name"] not in mnames]

    def list(self) -> dict:
        d = self._read()
        mark = lambda items, kind: [{**{k: v for k, v in c.items() if k not in SECRET}, "has_password": bool(c.get("password")), "origin": "ui"} for c in items]  # noqa: E731
        ui_s = {c["name"] for c in d["sources"]}
        ui_m = {c["name"] for c in d["mail"]}
        cfg_s = [{k: v for k, v in c.items() if k not in SECRET and k != "_ui"} | {"origin": "config"} for c in self.app.cfg.get("sources", []) if c["name"] not in ui_s]
        cfg_m = [{k: v for k, v in c.items() if k not in SECRET and k != "_ui"} | {"origin": "config"} for c in (self.app.cfg.get("mail") or {}).get("imap", []) if c["name"] not in ui_m]
        return {"sources": mark(d["sources"], "s") + cfg_s, "mail": mark(d["mail"], "m") + cfg_m}

    # ---------------- validation ----------------
    def _clean(self, spec: dict, keys, existing: dict | None) -> dict:
        c = {k: spec[k] for k in keys if k in spec and spec[k] not in (None, "")}
        if existing is not None and not c.get("password") and existing.get("password"):
            c["password"] = existing["password"]                      # left blank in the form = keep the stored one
        for k in ("port", "max_mb", "schedule_minutes", "level"):
            if k in c:
                try:
                    c[k] = int(c[k])
                except (TypeError, ValueError):
                    raise DmsError(400, f"숫자로 입력하세요: {k}")
        for k in ("include", "exclude", "allowed_senders"):
            if isinstance(c.get(k), str):
                c[k] = [x.strip() for x in c[k].replace("\n", ",").split(",") if x.strip()]
        for k in ("tls", "ssl"):
            if k in c:
                c[k] = bool(c[k])
        return c

    def save_source(self, spec: dict) -> dict:
        with self._lock:
            d = self._read()
            name = str(spec.get("name", "")).strip()
            old = next((c for c in d["sources"] if c["name"] == name), None)
            if old is None and any(c["name"] == name for c in self.app.cfg.get("sources", []) if not c.get("_ui")):
                raise DmsError(409, "설정 파일(config.json)에 같은 이름의 저장소가 있습니다. 다른 이름을 쓰세요")
            c = self._clean({**spec, "name": name}, SRC_KEYS, old)
            c.setdefault("type", "path")
            if c["type"] == "path" and c.get("mode", "reference") == "reference" and not c.get("acl"):
                c["acl"] = {"roles": ["admin"], "level": 99}          # fail closed until someone chooses who may read
            try:
                SourceManager._validate(dict(c))
            except SystemExit as e:
                raise DmsError(400, str(e).replace("sources: ", "").replace(f"sources[{name}]: ", ""))
            d["sources"] = [x for x in d["sources"] if x["name"] != name] + [c]
            self._write(d)
        self.apply()
        return {"saved": name}

    def delete_source(self, name: str) -> dict:
        with self._lock:
            d = self._read()
            if not any(c["name"] == name for c in d["sources"]):
                raise DmsError(404, "화면에서 만든 저장소만 삭제할 수 있습니다 (config.json 항목은 파일에서 지우세요)")
            d["sources"] = [c for c in d["sources"] if c["name"] != name]
            self._write(d)
        self.apply()
        return {"deleted": name}

    def save_mail(self, spec: dict) -> dict:
        with self._lock:
            d = self._read()
            name = str(spec.get("name", "")).strip()
            if not NAME_RE.fullmatch(name):
                raise DmsError(400, "이름은 한글·영문·숫자·_ . - 40자까지입니다")
            old = next((c for c in d["mail"] if c["name"] == name), None)
            if old is None and any(c["name"] == name for c in (self.app.cfg.get("mail") or {}).get("imap", []) if not c.get("_ui")):
                raise DmsError(409, "설정 파일(config.json)에 같은 이름의 메일 계정이 있습니다")
            c = self._clean({**spec, "name": name}, MAIL_KEYS, old)
            if not c.get("host") or not c.get("user"):
                raise DmsError(400, "메일 서버 주소와 계정이 필요합니다")
            c.setdefault("register", "both")
            if c["register"] not in ("message", "attachments", "both"):
                raise DmsError(400, "등록 방식은 message / attachments / both 입니다")
            d["mail"] = [x for x in d["mail"] if x["name"] != name] + [c]
            self._write(d)
        self.apply()
        return {"saved": name}

    def delete_mail(self, name: str) -> dict:
        with self._lock:
            d = self._read()
            if not any(c["name"] == name for c in d["mail"]):
                raise DmsError(404, "화면에서 만든 메일 계정만 삭제할 수 있습니다")
            d["mail"] = [c for c in d["mail"] if c["name"] != name]
            self._write(d)
        self.apply()
        return {"deleted": name}

    def apply(self):
        with self.app.lock:
            self.merge_into(self.app.cfg)
            self.app.sources.reload()
            self.app.mailbox.reload()

    # ---------------- connection tests (no data is read or changed) ----------------
    def _with_stored_password(self, spec: dict, kind: str) -> dict:
        spec = dict(spec)
        if not spec.get("password") and spec.get("name"):
            old = next((c for c in self._read()[kind] if c["name"] == spec["name"]), None)
            if old and old.get("password"):
                spec["password"] = old["password"]
        return spec

    def test_source(self, spec: dict) -> dict:
        c = self._with_stored_password(spec, "sources")
        t = c.get("type", "path")
        try:
            if t == "path":
                p = Path(c.get("path", ""))
                if not (p.is_absolute() or str(c.get("path", "")).startswith("\\\\")):
                    p = self.app._p(c.get("path", ""))
                if not p.exists():
                    return {"ok": False, "message": f"경로를 찾을 수 없습니다: {p}. 서버 PC에서 이 경로가 열리는지(네트워크 드라이브 연결·UNC 접근 권한)를 확인하세요."}
                if not p.is_dir():
                    return {"ok": False, "message": "폴더가 아닙니다"}
                n = files = 0
                sample = []
                for root, _dirs, fs in os.walk(p):
                    for f in fs:
                        files += 1
                        if len(sample) < 5:
                            sample.append(os.path.relpath(os.path.join(root, f), p))
                    n += 1
                    if files > 2000 or n > 500:
                        break
                return {"ok": True, "message": f"연결됨 · 폴더 {n}개, 파일 {files}{'+' if files > 2000 else ''}개 확인", "sample": sample}
            if t == "ftp":
                ftp = _ftp_session(c)
                try:
                    names = ftp.nlst(c.get("path", "/"))[:5]
                finally:
                    try:
                        ftp.quit()
                    except Exception:
                        pass
                return {"ok": True, "message": "FTP 로그인 성공", "sample": [str(x) for x in names]}
            if t == "webdav":
                with _dav_request(c, c["url"], "PROPFIND", {"Depth": "0"}) as r:
                    return {"ok": True, "message": f"WebDAV 응답 {r.status}"}
            return {"ok": False, "message": "알 수 없는 형식"}
        except (OSError, SourceError, ValueError, KeyError) as e:
            return {"ok": False, "message": f"{type(e).__name__}: {str(e)[:200]}"}
        except Exception as e:  # noqa: BLE001  (ftplib / urllib errors are many)
            return {"ok": False, "message": f"{type(e).__name__}: {str(e)[:200]}"}

    def test_mail(self, spec: dict) -> dict:
        c = self._with_stored_password(spec, "mail")
        try:
            port = int(c.get("port") or (993 if c.get("ssl", True) else 143))
            m = imaplib.IMAP4_SSL(c["host"], port, ssl_context=ssl.create_default_context(), timeout=20) if c.get("ssl", True) else imaplib.IMAP4(c["host"], port, timeout=20)
            try:
                m._encoding = "utf-8"                                    # non-ASCII passwords
                m.login(c.get("user", ""), c.get("password", ""))
                typ, data = m.select(c.get("mailbox", "INBOX"), readonly=True)
                if typ != "OK":
                    return {"ok": False, "message": f"메일함을 열 수 없습니다: {c.get('mailbox', 'INBOX')}"}
                t2, unseen = m.uid("SEARCH", None, "UNSEEN")
                n = len(unseen[0].split()) if t2 == "OK" and unseen and unseen[0] else 0
                return {"ok": True, "message": f"로그인 성공 · 전체 {int(data[0] or 0)}통, 읽지 않음 {n}통"}
            finally:
                try:
                    m.logout()
                except Exception:
                    pass
        except imaplib.IMAP4.error as e:
            return {"ok": False, "message": f"로그인/명령 실패: {str(e)[:150]} (계정·비밀번호·앱 비밀번호, IMAP 사용 설정을 확인하세요)"}
        except (OSError, KeyError, ValueError) as e:
            return {"ok": False, "message": f"서버에 연결할 수 없습니다: {type(e).__name__} {str(e)[:150]} (주소·포트·SSL, 방화벽을 확인하세요)"}
