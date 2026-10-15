"""E-mail -> documents: a shared mailbox (IMAP) becomes an inbox for the platform.

  "mail": {"imap": [{"name": "문서수신함", "host": "mail.example", "port": 993, "ssl": true, "user_env": "EDM_IMAP_USER", "password_env": "EDM_IMAP_PASS",
      "mailbox": "INBOX", "register": "both",               # message (.eml) | attachments | both
      "folder": "메일수신", "doc_type": "GEN", "unknown_sender": "skip",   # or "fallback" + "fallback_owner"
      "allowed_senders": ["*@iccjeju.example", "*@jto.example"], "max_mb": 25, "move_to": "Processed", "schedule_minutes": 10,
      "rules": [{"subject": "#계약", "folder": "MICE유치팀/계약", "doc_type": "CT", "tags": ["계약"]},
                {"subject": "re:\\\\[EVT-\\\\d{4}\\\\]", "folder": "MICE유치팀/행사"}, {"to": "edm+인사@*", "folder": "인사팀"}]}]}

Staff forward (or Cc/Bcc) a mail to the mailbox; the platform registers it in the SENDER's name (so folder permissions, security level
and audit apply as if they uploaded it themselves). Unknown senders are not registered. Each message is processed once (Message-ID)."""
from __future__ import annotations

import email
import fnmatch
import hashlib
import imaplib
import json
import logging
import os
import re
import ssl
import time
from datetime import datetime, timezone
from email import policy
from email.utils import parseaddr

from .errors import DmsError

log = logging.getLogger("edm_ai")
BLOCKED = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".dll", ".js", ".vbs", ".ps1", ".jar", ".sh", ".apk", ".app", ".lnk"}
SAFE_NAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MailIngest:
    def __init__(self, app):
        self.app = app
        self.cfgs = {}
        self.running: set = set()
        self.reload()

    def reload(self):
        self.cfgs = {}
        for c in (self.app.cfg.get("mail") or {}).get("imap", []):
            if not c.get("name") or not c.get("host"):
                raise SystemExit("mail.imap: name and host are required")
            self.cfgs[c["name"]] = c

    # ---------------- helpers ----------------
    def _connect(self, c):
        port = int(c.get("port", 993 if c.get("ssl", True) else 143))
        if c.get("ssl", True):
            m = imaplib.IMAP4_SSL(c["host"], port, ssl_context=ssl.create_default_context(), timeout=60)
        else:
            m = imaplib.IMAP4(c["host"], port, timeout=60)
        user = os.environ.get(c.get("user_env", ""), "") if c.get("user_env") else c.get("user", "")
        pw = os.environ.get(c.get("password_env", ""), "") if c.get("password_env") else c.get("password", "")
        m._encoding = "utf-8"
        m.login(user, pw)
        return m

    @staticmethod
    def _match_rule(rule: dict, subject: str, sender: str, recipients: list[str]) -> bool:
        s = rule.get("subject")
        if s:
            ok = re.search(s[3:], subject) if s.startswith("re:") else s.lower() in subject.lower()
            if not ok:
                return False
        if rule.get("from") and not fnmatch.fnmatch(sender, rule["from"].lower()):
            return False
        if rule.get("to") and not any(fnmatch.fnmatch(r, rule["to"].lower()) for r in recipients):
            return False
        return bool(s or rule.get("from") or rule.get("to"))

    def _owner(self, c, sender: str):
        uid = self.app.external.get(("email", sender))
        if uid:
            return self.app.users[uid]
        if c.get("unknown_sender") == "fallback" and c.get("fallback_owner") in self.app.users:
            return self.app.users[c["fallback_owner"]]
        return None

    # ---------------- status ----------------
    def status(self) -> list[dict]:
        out = []
        db = self.app.store.db
        for name, c in self.cfgs.items():
            last = db.execute("SELECT ts,ok,stats FROM source_runs WHERE source=? ORDER BY id DESC LIMIT 1", ("mail:" + name,)).fetchone()
            counts = dict(db.execute("SELECT status, COUNT(*) FROM mail_items WHERE account=? GROUP BY status", (name,)).fetchall())
            out.append({"name": name, "host": c["host"], "mailbox": c.get("mailbox", "INBOX"), "register": c.get("register", "both"), "schedule_minutes": c.get("schedule_minutes"),
                        "counts": counts, "running": name in self.running, "last": {"ts": last[0], "ok": bool(last[1]), "stats": json.loads(last[2])} if last else None})
        return out

    def items(self, name: str, limit: int = 200) -> list[dict]:
        return [{"subject": r[0], "sender": r[1], "status": r[2], "docs": json.loads(r[3] or "[]"), "note": r[4], "ts": r[5]} for r in self.app.store.db.execute(
            "SELECT subject,sender,status,doc_ids,note,ts FROM mail_items WHERE account=? ORDER BY id DESC LIMIT ?", (name, limit)).fetchall()]

    def due(self) -> list[str]:
        out = []
        for name, c in self.cfgs.items():
            m = c.get("schedule_minutes")
            if not m or name in self.running:
                continue
            last = self.app.store.db.execute("SELECT ts FROM source_runs WHERE source=? ORDER BY id DESC LIMIT 1", ("mail:" + name,)).fetchone()
            if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last[0])).total_seconds() > int(m) * 60:
                out.append(name)
        return out

    # ---------------- run ----------------
    def run(self, name: str, dry_run: bool = False) -> dict:
        c = self.cfgs.get(name)
        if not c:
            raise DmsError(404, "메일 계정을 찾을 수 없습니다")
        if name in self.running:
            raise DmsError(409, "이미 실행 중입니다")
        self.running.add(name)
        stats = {"dry_run": dry_run, "registered": 0, "attachments": 0, "duplicate": 0, "unknown_sender": 0, "not_allowed": 0, "blocked": 0, "error": 0, "seen": 0, "errors": []}
        m = None
        try:
            try:
                m = self._connect(c)
                typ, _ = m.select(c.get("mailbox", "INBOX"), readonly=dry_run)
                if typ != "OK":
                    raise imaplib.IMAP4.error("mailbox not available")
                typ, data = m.uid("SEARCH", None, c.get("search", "UNSEEN"))
                uids = (data[0].split() if typ == "OK" and data and data[0] else [])[: int(c.get("max_per_run", 100))]
            except (imaplib.IMAP4.error, OSError) as e:
                stats["error"] = 1
                stats["errors"].append(f"메일 서버 연결 실패: {type(e).__name__}: {str(e)[:150]}")
                self._finish(name, stats, False, dry_run)
                return stats
            for uid in uids:
                stats["seen"] += 1
                try:
                    st = self._one(m, c, name, uid, dry_run, stats)
                except Exception as e:                                           # one bad message never stops the run
                    st = "error"
                    stats["errors"].append(f"uid {uid.decode()}: {type(e).__name__}: {str(e)[:120]}")
                    self._record(name, f"uid:{uid.decode()}", "(처리 실패)", "", "error", [], f"{type(e).__name__}", dry_run)
                if st in ("error",):
                    stats["error"] += 1
            stats["errors"] = stats["errors"][:30]
            self._finish(name, stats, stats["error"] == 0, dry_run)
            return stats
        finally:
            self.running.discard(name)
            if m is not None:
                try:
                    m.logout()
                except Exception:
                    pass

    def _finish(self, name, stats, ok, dry_run):
        if not dry_run:
            self.app.store.db.execute("INSERT INTO source_runs(source,kind,ts,ok,stats) VALUES(?,?,?,?,?)", ("mail:" + name, "mail", _now(), int(ok), json.dumps(stats, ensure_ascii=False)))
            self.app.store.db.commit()

    def _record(self, account, mid, subject, sender, status, docs, note, dry_run):
        if dry_run:
            return
        self.app.store.db.execute("INSERT OR REPLACE INTO mail_items(account,message_id,subject,sender,status,doc_ids,note,ts) VALUES(?,?,?,?,?,?,?,?)",
                                  (account, mid, subject[:200], sender[:200], status, json.dumps(docs), note[:200], _now()))
        self.app.store.db.commit()

    def _one(self, m, c, name, uid, dry_run, stats) -> str:
        typ, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
        raw = next((x[1] for x in data if isinstance(x, tuple)), None) if typ == "OK" else None
        if not raw:
            raise ValueError("empty fetch")
        if len(raw) > int(c.get("max_mb", 25)) * 1048576:
            self._record(name, "uid:" + uid.decode(), "(너무 큰 메일)", "", "too_large", [], f"{len(raw) // 1048576}MB", dry_run)
            self._mark(m, c, uid, dry_run)
            return "too_large"
        msg = email.message_from_bytes(raw, policy=policy.default)
        mid = (msg["Message-ID"] or "").strip() or "sha:" + hashlib.sha256(raw).hexdigest()[:32]
        subject = str(msg["Subject"] or "(제목 없음)")
        sender = parseaddr(str(msg["From"] or ""))[1].lower()
        recipients = [parseaddr(x)[1].lower() for x in re.split(r",\s*(?=[^,]*@)", str(msg["To"] or "") + "," + str(msg["Cc"] or "")) if "@" in x]
        db = self.app.store.db
        if db.execute("SELECT 1 FROM mail_items WHERE account=? AND message_id=? AND status IN ('registered','duplicate')", (name, mid)).fetchone():
            self._mark(m, c, uid, dry_run)
            stats["duplicate"] += 1
            return "duplicate"
        allowed = c.get("allowed_senders")
        if allowed and not any(fnmatch.fnmatch(sender, a.lower()) for a in allowed):
            stats["not_allowed"] += 1
            self._record(name, mid, subject, sender, "not_allowed", [], "허용되지 않은 발신자", dry_run)
            self._mark(m, c, uid, dry_run)
            return "not_allowed"
        owner = self._owner(c, sender)
        if not owner:
            stats["unknown_sender"] += 1
            self._record(name, mid, subject, sender, "unknown_sender", [], "사용자와 연결되지 않은 발신자", dry_run)
            self._mark(m, c, uid, dry_run)
            return "unknown_sender"
        rule = next((r for r in c.get("rules", []) if self._match_rule(r, subject, sender, recipients)), {})
        folder_path = rule.get("folder") or c.get("folder") or "메일수신"
        doc_type = rule.get("doc_type") or c.get("doc_type", "GEN")
        tags = list(dict.fromkeys(["메일"] + list(rule.get("tags", []))))
        props = {"메일제목": subject[:190], "보낸사람": sender, "수신일": str(msg["Date"] or "")[:60], "메일계정": name}
        mode = c.get("register", "both")
        docs, d = [], self.app.dms
        if dry_run:
            stats["registered"] += 1
            return "registered"
        with self.app.lock:
            fid = d.ensure_folder_path(folder_path, c.get("folder_entries") if not rule.get("folder") else rule.get("folder_entries"))
            main_id = None
            if mode in ("message", "both"):
                fn = (SAFE_NAME.sub("_", subject)[:80] or "메일") + ".eml"
                try:
                    doc = d.register(owner, fn, raw, folder_id=fid, doc_type=doc_type, tags=tags, props=props, level=rule.get("level", c.get("level", 0)),
                                     entries=rule.get("entries"), comment=f"메일 수신 ({name})")
                    main_id = doc["id"]
                    docs.append(main_id)
                except DmsError as e:
                    if e.code == 422:
                        self._record(name, mid, subject, sender, "blocked", [], e.msg, dry_run)
                        self._mark(m, c, uid, dry_run)
                        stats["blocked"] += 1
                        return "blocked"
                    raise
            if mode in ("attachments", "both"):
                for part in msg.iter_attachments():
                    fn = part.get_filename() or ""
                    data = part.get_payload(decode=True) or b""
                    ext = ("." + fn.rsplit(".", 1)[-1].lower()) if "." in fn else ""
                    if not fn or not data or ext in BLOCKED or (part.get_content_disposition() == "inline" and len(data) < 20000 and ext in (".png", ".jpg", ".gif", ".jpeg")):
                        continue
                    if part.get_content_type() == "message/rfc822":
                        continue
                    try:
                        a = d.register(owner, SAFE_NAME.sub("_", fn)[:120], data, folder_id=fid, doc_type=doc_type, tags=tags + ["메일첨부"], props=props,
                                       level=rule.get("level", c.get("level", 0)), entries=rule.get("entries"), comment=f"메일 첨부 ({name})")
                        docs.append(a["id"])
                        stats["attachments"] += 1
                        if main_id:
                            d.link_docs(owner, main_id, a["id"])
                    except DmsError as e:
                        stats["errors"].append(f"{fn}: {e.msg}")
        stats["registered"] += 1
        self._record(name, mid, subject, sender, "registered", docs, "", dry_run)
        self._mark(m, c, uid, dry_run)
        return "registered"

    def _mark(self, m, c, uid, dry_run):
        if dry_run:
            return
        m.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        if c.get("move_to"):
            if m.uid("COPY", uid, c["move_to"])[0] == "OK":
                m.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                m.expunge()
