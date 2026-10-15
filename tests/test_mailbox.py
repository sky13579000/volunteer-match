import json
import os
import shutil
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeservers  # noqa: E402
import test_platform  # noqa: E402

from edm_ai.config import App  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


def mail(subject, sender="kim@iccjeju.example", to="edm@iccjeju.example", body="본문입니다.", attach=(), mid=None, cc=""):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"], m["Date"] = subject, sender, to, "Wed, 14 Oct 2026 09:00:00 +0900"
    if cc:
        m["Cc"] = cc
    if mid is not False:
        m["Message-ID"] = mid or f"<{abs(hash(subject + sender))}@t>"
    m.set_content(body)
    for name, data in attach:
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return m.as_bytes()


class MailTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.tmp = Path(self.t.name)
        os.environ["T_IMAP_U"], os.environ["T_IMAP_P"] = "u", "p"
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()
        self.t.cleanup()

    def app(self, messages, **extra):
        srv = fakeservers.ImapServer(messages)
        self.servers.append(srv)
        shutil.copytree(test_platform.DEMO / "docs", self.tmp / "docs")
        shutil.copy(test_platform.DEMO / "users.json", self.tmp / "users.json")
        cfg = json.loads((test_platform.DEMO / "config.json").read_text("utf-8"))
        cfg["store"], cfg["structured"] = "store.db", None
        cfg.pop("entities", None)
        cfg.pop("sources", None)
        acct = {"name": "수신함", "host": "127.0.0.1", "port": srv.port, "ssl": False, "user_env": "T_IMAP_U", "password_env": "T_IMAP_P", "folder": "MICE유치팀/메일",
                "allowed_senders": ["*@iccjeju.example"], "rules": [{"subject": "#계약", "folder": "MICE유치팀/계약", "doc_type": "GEN", "tags": ["계약"]}], **extra}
        cfg["mail"] = {"imap": [acct]}
        (self.tmp / "config.json").write_text(json.dumps(cfg), "utf-8")
        a = App(str(self.tmp / "config.json"))
        self.srv = srv
        return a

    def test_message_and_attachments_are_registered_in_the_senders_name(self):
        a = self.app([mail("[행사] 대관 일정 문의", attach=[("일정표.md", "# 일정표\n\n메일첨부본문유니크".encode()), ("run.exe", b"MZ")])])
        st = a.mailbox.run("수신함")
        self.assertEqual((st["registered"], st["attachments"], st["error"]), (1, 1, 0))
        d = a.dms
        eml = d.q1("SELECT id,owner,folder_id FROM dms_docs WHERE title LIKE '%대관 일정 문의%'")
        self.assertEqual(eml["owner"], "kim")                                                        # sender -> user by e-mail
        self.assertEqual(d.folder_path(eml["folder_id"]), "MICE유치팀 / 메일")
        att = d.q1("SELECT id FROM dms_docs WHERE title LIKE '%일정표%' AND origin='managed'")
        self.assertIn(att["id"], [r["id"] for r in d.related(a.users["kim"], eml["id"])])             # attachment linked to the mail
        self.assertFalse(d.q("SELECT 1 FROM dms_docs WHERE title LIKE '%run%' AND origin='managed'"))  # executables are not registered
        got = d.get(a.users["kim"], att["id"])
        self.assertEqual((got["props"]["보낸사람"], "메일첨부" in got["tags"]), ("kim@iccjeju.example", True))
        self.assertTrue(any(h.ref.startswith("dms/") for h in a.engine.ask(a.users["kim"], "메일첨부본문유니크").evidence))

    def test_each_message_is_processed_once_and_marked_seen(self):
        a = self.app([mail("한 번만")])
        self.assertEqual(a.mailbox.run("수신함")["registered"], 1)
        self.assertEqual(a.mailbox.run("수신함")["seen"], 0)                                          # \Seen on the server
        self.srv.boxes["INBOX"][0]["flags"].clear()                                                  # even when it shows up as unread again ...
        st = a.mailbox.run("수신함")
        self.assertEqual((st["registered"], st["duplicate"]), (0, 1))                                # ... the Message-ID prevents a second copy
        self.assertEqual(a.dms.q1("SELECT COUNT(*) AS n FROM dms_docs WHERE title LIKE '%한 번만%'")["n"], 1)

    def test_rules_unknown_and_disallowed_senders(self):
        a = self.app([mail("#계약 갱신 건"), mail("외부에서 온 메일", sender="stranger@iccjeju.example"), mail("스팸", sender="x@evil.example"), mail("메일없는사용자", sender="nobody@iccjeju.example")])
        st = a.mailbox.run("수신함")
        self.assertEqual((st["registered"], st["unknown_sender"], st["not_allowed"]), (1, 2, 1))
        row = a.dms.q1("SELECT folder_id,tags FROM dms_docs WHERE title LIKE '%갱신%'")
        self.assertEqual(a.dms.folder_path(row["folder_id"]), "MICE유치팀 / 계약")
        self.assertIn("계약", json.loads(row["tags"]))
        self.assertFalse(a.dms.q("SELECT 1 FROM dms_docs WHERE title LIKE '%스팸%'"))                  # nothing from unknown/blocked senders
        statuses = {i["subject"]: i["status"] for i in a.mailbox.items("수신함")}
        self.assertEqual(statuses["스팸"], "not_allowed")

    def test_fallback_owner_and_register_modes(self):
        a = self.app([mail("대리등록", sender="nobody@iccjeju.example", attach=[("첨부.md", b"# x\n\nfallback")])], unknown_sender="fallback", fallback_owner="admin", register="attachments")
        st = a.mailbox.run("수신함")
        self.assertEqual((st["registered"], st["attachments"]), (1, 1))
        self.assertFalse(a.dms.q("SELECT 1 FROM dms_docs WHERE title LIKE '%대리등록%' AND origin='managed'"))      # attachments only: no .eml document
        rows = [r for r in a.dms.q("SELECT owner,tags FROM dms_docs WHERE origin='managed'") if "메일첨부" in json.loads(r["tags"])]
        self.assertEqual([r["owner"] for r in rows], ["admin"])

    def test_dry_run_changes_nothing_and_leaves_mail_unread(self):
        a = self.app([mail("미리보기")])
        before = a.dms.q1("SELECT COUNT(*) AS n FROM dms_docs")["n"]
        st = a.mailbox.run("수신함", dry_run=True)
        self.assertEqual(st["registered"], 1)
        self.assertEqual(a.dms.q1("SELECT COUNT(*) AS n FROM dms_docs")["n"], before)
        self.assertEqual(self.srv.boxes["INBOX"][0]["flags"], set())

    def test_processed_mail_is_moved_when_configured(self):
        a = self.app([mail("이동 대상")], move_to="Processed")
        a.mailbox.run("수신함")
        self.assertEqual((len(self.srv.boxes["INBOX"]), len(self.srv.boxes["Processed"])), (0, 1))

    def test_connection_and_login_failures_are_reported_not_raised(self):
        a = self.app([mail("x")])
        os.environ["T_IMAP_P"] = "wrong"
        st = a.mailbox.run("수신함")
        self.assertEqual(st["registered"], 0)
        self.assertIn("메일 서버 연결 실패", st["errors"][0])
        self.assertFalse(a.mailbox.status()[0]["last"]["ok"])
        os.environ["T_IMAP_P"] = "p"

    def test_privacy_block_policy_applies_to_mail(self):
        a = self.app([mail("개인정보 포함", body="홍길동 900101-1234567")])
        a.cfg["dlp"] = {"mode": "block"}
        st = a.mailbox.run("수신함")
        self.assertEqual((st["blocked"], st["registered"]), (1, 0))
        self.assertFalse(a.dms.q("SELECT 1 FROM dms_docs WHERE title LIKE '%개인정보 포함%'"))

    def test_status_counts(self):
        a = self.app([mail("a"), mail("b", sender="x@evil.example")])
        a.mailbox.run("수신함")
        s = a.mailbox.status()[0]
        self.assertEqual((s["counts"].get("registered"), s["counts"].get("not_allowed")), (1, 1))


if __name__ == "__main__":
    unittest.main()
