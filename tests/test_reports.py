import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.errors import DmsError  # noqa: E402


class Reports(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users

    def test_admin_only(self):
        for fn in (lambda: self.d.report(self.U["kim"]), lambda: self.d.report_csv(self.U["kim"], "types")):
            with self.assertRaises(DmsError) as cm:
                fn()
            self.assertEqual(cm.exception.code, 403)

    def test_numbers_match_the_repository(self):
        r = self.d.report(self.U["admin"])
        managed = self.d.q1("SELECT COUNT(*) AS n FROM dms_docs WHERE origin='managed' AND deleted_at IS NULL")["n"]
        self.assertEqual(r["kpis"]["documents"], managed)
        self.assertEqual(sum(x["count"] for x in r["registrations_by_month"]), managed)          # every managed doc was created in the last 12 months
        self.assertEqual(sum(x["count"] for x in r["documents_by_status"]), managed + r["kpis"]["external_documents"])
        self.assertEqual(sum(x["count"] for x in r["documents_by_level"]), managed)
        self.assertEqual(len(r["registrations_by_month"]), 12)
        self.assertEqual(len(r["ai_usage"]["daily"]), 30)
        self.assertEqual(r["kpis"]["legal_holds"], 1)                                             # seeded dispute document
        self.assertGreaterEqual(r["approvals"]["by_status"][0]["count"], 1)

    def test_ai_usage_counts_questions_not_document_actions(self):
        before = self.d.report(self.U["admin"])["ai_usage"]
        self.app.engine.ask(self.U["kim"], "제주관광공사 계약 금액")
        self.app.engine.ask(self.U["kim"], "오늘 점심 메뉴 추천")                                    # refused: counts as 'no evidence'
        after = self.d.report(self.U["admin"])["ai_usage"]
        self.assertEqual(sum(x["count"] for x in after["daily"]) - sum(x["count"] for x in before["daily"]), 2)
        self.assertEqual(after["no_evidence"] - before["no_evidence"], 1)

    def test_turnaround_and_pending_age(self):
        kim, park = self.U["kim"], self.U["park"]
        fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]
        doc = self.d.register(kim, "wf.md", "# 결재시간\n\n본문".encode(), folder_id=fid, doc_type="CT")
        self.d.submit(kim, doc["id"], ["park"])
        self.assertTrue([a for a in self.d.report(self.U["admin"])["approvals"]["pending_by_approver"] if a["user"] == "park"])
        self.d.approve(park, doc["id"], "ok")
        t = self.d.report(self.U["admin"])["approvals"]["turnaround_hours"]
        self.assertIsNotNone(t)
        self.assertGreaterEqual(t["n"], 1)
        self.assertLessEqual(t["avg"], 1.0)

    def test_top_documents_never_reveal_titles_the_admin_cannot_read(self):
        hr = self.d.q1("SELECT id,source FROM dms_docs WHERE source LIKE 'hr/%'")
        self.d.log(self.U["lee"], self.d.row(hr["id"]), "commented")
        r = self.d.report(self.U["admin"])
        item = [x for x in r["active_documents"] if x["doc_id"] == hr["id"]]
        self.assertTrue(item)
        self.assertIsNone(item[0]["title"])                                                       # shown by number only
        self.assertNotIn("성과평가", str(r))

    def test_csv_export(self):
        csv_text = self.d.report_csv(self.U["admin"], "types")
        self.assertTrue(csv_text.startswith("﻿유형,문서 수"))
        with self.assertRaises(DmsError):
            self.d.report_csv(self.U["admin"], "nope")

    def test_days_are_clamped(self):
        self.assertEqual(self.d.report(self.U["admin"], 99999)["days"], 365)
        self.assertEqual(self.d.report(self.U["admin"], 1)["days"], 7)


if __name__ == "__main__":
    unittest.main()
