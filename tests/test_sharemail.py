import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_mail  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


class ShareByMail(test_mail.MailCase):
    def gen(self, title="공유 메일"):
        return self.d.register(self.U["kim"], "g.md", f"# {title}\n\n본문 비밀내용 secretbody.".encode(), doc_type="GEN", title=title)

    def setUp(self):
        super().setUp()
        self.app.cfg.setdefault("notify", {})["base_url"] = "https://edm.iccjeju.example"

    def test_one_link_per_recipient(self):
        doc = self.gen()
        kim = self.U["kim"]
        r = self.d.send_share_mail(kim, doc["id"], ["a@partner.example", "b@partner.example", "A@partner.example"], "검토 부탁")
        self.assertEqual(len(r["sent"]), 2)
        links = []
        for addr in ("a@partner.example", "b@partner.example"):
            ms = self.mails_to(addr)
            self.assertEqual(len(ms), 1)
            self.assertNotIn("secretbody", ms[0]["body"])
            m = re.search(r"https://edm\.iccjeju\.example/s/(\S+)", ms[0]["body"])
            self.assertTrue(m)
            links.append(m.group(1))
        self.assertNotEqual(links[0], links[1])
        self.assertEqual(len(self.d.list_shares(kim, doc["id"])), 2)

    def test_password_by_mail_and_validation(self):
        doc = self.gen("비번 공유")
        kim = self.U["kim"]
        r = self.d.send_share_mail(kim, doc["id"], "c@partner.example", password="Pw-12345", password_mode="mail")
        self.assertIsNone(r["password"])
        self.assertEqual(len(self.mails_to("c@partner.example")), 2)
        for bad in ([], ["not-an-email"]):
            with self.assertRaises(DmsError):
                self.d.send_share_mail(kim, doc["id"], bad)

    def test_permission_required(self):
        doc = self.gen("권한 시험")
        with self.assertRaises(DmsError):
            self.d.send_share_mail(self.U["lee"], doc["id"], ["z@partner.example"])


if __name__ == "__main__":
    unittest.main()
