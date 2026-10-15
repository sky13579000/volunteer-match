import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


class Extras(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]

    def test_announcements(self):
        with self.assertRaises(DmsError):
            self.d.save_announcement(self.U["kim"], {"title": "x"})
        with self.assertRaises(DmsError):
            self.d.save_announcement(self.U["admin"], {"title": ""})
        a = self.d.save_announcement(self.U["admin"], {"title": "시스템 점검 안내", "body": "토요일 02시", "pinned": True})
        self.d.save_announcement(self.U["admin"], {"title": "지난 공지", "expires": "2020-01-01"})
        titles = [n["title"] for n in self.d.announcements(self.U["kim"])]
        self.assertEqual(titles[0], "시스템 점검 안내")
        self.assertNotIn("지난 공지", titles)
        self.assertIn("notice", [n["kind"] for n in self.d.notifications(self.U["kim"])["items"]])
        self.d.delete_announcement(self.U["admin"], a["id"])
        self.assertNotIn("시스템 점검 안내", [n["title"] for n in self.d.announcements(self.U["kim"])])

    def test_duplicate_report_hides_unreadable_titles(self):
        body = b"# dup\n\nduplicate-report-body-unique"
        a = self.d.register(self.U["kim"], "a.md", body, folder_id=self.fid, doc_type="GEN", title="복사본 A")
        self.d.register(self.U["kim"], "b.md", body, folder_id=self.fid, doc_type="GEN", title="복사본 B", entries=[{"type": "user", "id": "kim", "role": "manager"}], inherit=False)
        with self.assertRaises(DmsError):
            self.d.duplicate_report(self.U["kim"])
        rep = self.d.duplicate_report(self.U["admin"])
        g = next(g for g in rep["groups"] if any(i["doc_id"] == a["id"] for i in g["items"]))
        self.assertEqual(g["count"], 2)
        self.assertIn(None, [i["title"] for i in g["items"]])                # admin cannot read the restricted copy
        self.assertGreater(rep["wasted_bytes"], 0)

    def test_suggest(self):
        txt = "# 용역 계약서\n\n계약 금액은 1억원이다. 지체상금은 계약금액의 0.1%이다. 계약 기간 동안 비밀 유지. 계약 해지 조항. 지체상금 부과.".encode()
        r = self.d.suggest(self.U["kim"], "contract.md", txt)
        self.assertEqual(r["category"], "계약서")
        self.assertEqual(r["doc_type"], "CT")
        self.assertIn("지체상금", r["tags"])
        self.assertTrue(self.d.suggest(self.U["kim"], "x.bin", b"\x00\x01")["category"])


if __name__ == "__main__":
    unittest.main()
