import email
import email.policy
import socketserver
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.mailer import Mailer, clean  # noqa: E402


class FakeSmtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.mails: list[dict] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    class Handler(socketserver.StreamRequestHandler):
        def send(self, s):
            self.wfile.write((s + "\r\n").encode())

        def handle(self):
            self.send("220 fake ESMTP")
            mail = {"to": [], "data": ""}
            while True:
                line = self.rfile.readline().decode("utf-8", "replace").strip()
                if not line:
                    return
                cmd = line.split(" ")[0].upper()
                if cmd in ("EHLO", "HELO"):
                    self.send("250 localhost")
                elif cmd == "MAIL":
                    mail = {"from": line, "to": [], "data": ""}
                    self.send("250 OK")
                elif cmd == "RCPT":
                    mail["to"].append(line.split(":", 1)[1].strip(" <>"))
                    self.send("250 OK")
                elif cmd == "DATA":
                    self.send("354 go")
                    buf = []
                    while True:
                        l = self.rfile.readline().decode("utf-8", "replace")
                        if l in (".\r\n", ""):
                            break
                        buf.append(l)
                    mail["data"] = "".join(buf)
                    self.server.mails.append(mail)
                    self.send("250 OK")
                elif cmd == "QUIT":
                    self.send("221 bye")
                    return
                else:
                    self.send("250 OK")


class MailCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.smtp = FakeSmtp()
        cls.d, cls.U = cls.app.dms, cls.app.users
        cls.app.mailer = Mailer({"host": "127.0.0.1", "port": cls.smtp.server_address[1], "from": "edm@iccjeju.example"}, "https://edm.iccjeju.example")

    @classmethod
    def tearDownClass(cls):
        cls.smtp.shutdown()
        cls.smtp.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.app.mailer.flush()
        self.smtp.mails.clear()

    def mails_to(self, addr):
        self.assertTrue(self.app.mailer.flush(10))
        out = []
        for m in self.smtp.mails:
            if addr in m["to"]:
                msg = email.message_from_string(m["data"], policy=email.policy.default)   # decode like a real mail client (base64 / RFC 2047)
                out.append({**m, "msg": msg, "body": msg.get_content(), "subject": str(msg["Subject"])})
        return out

    def ct(self, title="계약 메일시험"):
        return self.d.register(self.U["kim"], "c.md", f"# {title}\n\n본문 비밀내용 secretbody.".encode(), doc_type="CT", title=title)

    def test_approval_request_mail_has_link_but_no_document_text(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park"], note="메모에 적은 내용")
        got = self.mails_to("park@iccjeju.example")
        self.assertEqual(len(got), 1)
        body = got[0]["body"]
        self.assertIn("결재 요청", got[0]["subject"])
        self.assertIn(f"https://edm.iccjeju.example/#/doc/{r['id']}?tab=approval", body)
        self.assertIn(r["doc_no"], body)
        self.assertNotIn("secretbody", body)                       # document text never leaves the system
        self.assertNotIn("메모에 적은 내용", body)
        self.assertIn("edm@iccjeju.example", got[0]["from"])

    def test_comment_mail_never_contains_the_comment(self):
        r = self.d.register(self.U["kim"], "d.md", "# 댓글\n\n본문.".encode(), doc_type="GEN")
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "user", "id": "park", "role": "viewer"}, {"type": "dept", "id": "mice", "role": "editor"}])
        self.d.add_comment(self.U["kim"], r["id"], "@park 극비 금액 9999억 공개 금지")
        got = self.mails_to("park@iccjeju.example")[0]
        self.assertIn("언급", got["subject"])
        self.assertNotIn("9999", got["body"] + got["subject"])
        self.assertNotIn("극비", got["body"] + got["subject"])

    def test_user_can_opt_out_and_back_in(self):
        self.assertTrue(self.d.get_prefs(self.U["park"])["email"])
        self.d.set_prefs(self.U["park"], False)
        r = self.ct("옵트아웃 시험")
        self.d.submit(self.U["kim"], r["id"], ["park"])
        self.assertEqual(self.mails_to("park@iccjeju.example"), [])
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='park' AND doc_id=?", (r["id"],)))   # in-app copy is unaffected
        self.d.set_prefs(self.U["park"], True)
        r2 = self.ct("옵트인 시험")
        self.d.submit(self.U["kim"], r2["id"], ["park"])
        self.assertEqual(len(self.mails_to("park@iccjeju.example")), 1)

    def test_header_injection_is_neutralised(self):
        self.assertEqual(clean("a\r\nBcc: evil@x.com"), "a Bcc: evil@x.com")
        r = self.ct("제목\r\nBcc: evil@example.com\r\nX-Evil: 1")
        self.d.submit(self.U["kim"], r["id"], ["park"])
        got = self.mails_to("park@iccjeju.example")[0]
        head = got["data"].split("\r\n\r\n", 1)[0]
        self.assertNotIn("\nBcc:", head)
        self.assertNotIn("\nX-Evil:", head)
        self.assertIsNone(got["msg"]["Bcc"])
        self.assertIsNone(got["msg"]["X-Evil"])
        self.assertEqual(got["to"], ["park@iccjeju.example"])                                  # nobody else was added as a recipient

    def test_title_can_be_left_out_and_users_without_email_get_none(self):
        self.app.mailer.include_title = False
        try:
            r = self.ct("제목은 숨김 제목시험")
            self.d.submit(self.U["kim"], r["id"], ["park"])
            self.assertNotIn("제목시험", self.mails_to("park@iccjeju.example")[0]["body"])
        finally:
            self.app.mailer.include_title = True
        saved = dict(self.app.emails)
        self.app.emails.pop("lee")
        try:
            r = self.ct("메일주소 없음")
            self.d.submit(self.U["kim"], r["id"], ["lee"])
            self.assertEqual(self.mails_to("lee@iccjeju.example"), [])
            self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='lee' AND doc_id=?", (r["id"],)))
        finally:
            self.app.emails.update(saved)

    def test_smtp_outage_never_breaks_the_workflow(self):
        dead = Mailer({"host": "127.0.0.1", "port": 1, "timeout": 0.5}, "")
        keep, self.app.mailer = self.app.mailer, dead
        try:
            r = self.ct("메일서버 장애")
            out = self.d.submit(self.U["kim"], r["id"], ["park"])
            self.assertEqual(out["workflow"]["status"], "pending")                            # the approval request still went through
            self.assertTrue(dead.flush(15))
            self.assertGreaterEqual(dead.failed, 1)
            r2 = self.ct("장애 후 계속")                                                       # the worker survived
            self.d.submit(self.U["kim"], r2["id"], ["park"])
        finally:
            self.app.mailer = keep

    def test_mail_is_off_without_configuration(self):
        keep, self.app.mailer = self.app.mailer, None
        try:
            self.assertFalse(self.d.get_prefs(self.U["park"])["email_available"])
            r = self.ct("설정 없음")
            self.d.submit(self.U["kim"], r["id"], ["park"])
        finally:
            self.app.mailer = keep


if __name__ == "__main__":
    unittest.main()
