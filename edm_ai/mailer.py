from __future__ import annotations

import logging
import os
import queue
import re
import smtplib
import threading
from email.message import EmailMessage

log = logging.getLogger("edm_ai")
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
KINDS = {
    "approval_request": "결재 요청이 도착했습니다",
    "approved": "요청하신 결재가 승인되었습니다",
    "rejected": "요청하신 결재가 반려되었습니다",
    "cancelled": "결재 요청이 회수되었습니다",
    "expiring": "문서가 곧 만료됩니다",
    "expired": "문서가 만료되었습니다",
    "mention": "문서에서 회원님이 언급되었습니다",
    "comment": "내 문서에 댓글이 달렸습니다",
}


def clean(s: str, limit: int = 200) -> str:
    """No CR/LF/control characters ever reach a header (header injection) and nothing is unbounded."""
    return _CTRL.sub(" ", str(s)).strip()[:limit]


class Mailer:
    """Optional SMTP notifier. Sends on a background thread so a slow mail server never blocks a request.
    Mail bodies carry only the event, the document number/title and a link: never document text or comment text."""

    def __init__(self, cfg: dict, base_url: str = ""):
        self.host, self.port = cfg["host"], int(cfg.get("port", 25))
        self.sender = clean(cfg.get("from", "edm-ai@localhost"))
        self.starttls, self.timeout = bool(cfg.get("starttls", False)), float(cfg.get("timeout", 10))
        self.user = os.environ.get(cfg.get("user_env", ""), "") if cfg.get("user_env") else ""
        self.password = os.environ.get(cfg.get("password_env", ""), "") if cfg.get("password_env") else ""
        self.include_title = bool(cfg.get("include_title", True))
        self.base_url = (base_url or cfg.get("base_url", "")).rstrip("/")
        self.q: queue.Queue = queue.Queue(maxsize=1000)
        self.sent = self.failed = 0
        threading.Thread(target=self._run, daemon=True, name="edm-mailer").start()

    def build(self, to: str, kind: str, doc_no: str | None, title: str | None, doc_id) -> EmailMessage:
        what = KINDS.get(kind, "새 알림이 있습니다")
        subject = f"[ICC JEJU] {what}" + (f" - {clean(doc_no, 40)}" if doc_no else "")
        lines = [what + "."]
        if doc_no and title and self.include_title:
            lines.append(f"문서: {clean(doc_no, 40)} {clean(title, 120)}")
        if self.base_url and doc_id:
            lines.append(f"바로가기: {self.base_url}/#/doc/{int(doc_id)}" + ("?tab=approval" if kind == "approval_request" else ""))
        lines.append("\n이 메일은 ICC JEJU 문서·AI 플랫폼이 자동으로 보냈습니다. 알림 설정에서 메일 수신을 끌 수 있습니다.")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, clean(to), clean(subject)
        msg.set_content("\n".join(lines))
        return msg

    def enqueue(self, msg: EmailMessage):
        try:
            self.q.put_nowait(msg)
        except queue.Full:
            self.failed += 1
            log.warning("mail queue full: dropping a notification")

    def _send(self, msg: EmailMessage):
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
            if self.starttls:
                s.starttls()
            if self.user:
                s.login(self.user, self.password)
            s.send_message(msg)

    def _run(self):
        while True:
            msg = self.q.get()
            try:
                try:
                    self._send(msg)
                except (OSError, smtplib.SMTPException):
                    self._send(msg)                       # one retry for a transient failure
                self.sent += 1
            except Exception:  # noqa: BLE001  never let a mail problem kill the worker
                self.failed += 1
                log.exception("mail delivery failed (to=%s)", msg["To"])
            finally:
                self.q.task_done()

    def flush(self, timeout: float = 10) -> bool:
        """Wait until the queue is drained (used by tests and graceful shutdown)."""
        t = threading.Thread(target=self.q.join, daemon=True)
        t.start()
        t.join(timeout)
        return not t.is_alive()
