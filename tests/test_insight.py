import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402

CT1 = "# 용역 계약서\n\n## 계약 금액\n\n계약 금액은 4억 8천만원으로 한다.\n\n## 지체상금\n\n납기 지연 시 지체상금을 부과한다.\n\n## 비밀 유지\n\n상대방의 비밀을 누설하지 않는다.".encode()
CT2 = "# 용역 계약서\n\n## 계약 금액\n\n계약 금액은 5억원으로 한다.\n\n## 지체상금\n\n납기 지연 시 지체상금을 부과한다.\n\n## 분쟁 해결\n\n관할 법원은 제주지방법원으로 한다.\n\n계약 기간은 2026-12-31 까지.".encode()


class Insight(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]

    def test_clause_check_lists_present_and_missing(self):
        doc = self.d.register(self.U["kim"], "c.md", CT1, folder_id=self.fid, doc_type="GEN", title="조항 점검")
        r = self.d.clause_check(self.U["kim"], doc["id"])
        st = {i["key"]: i["present"] for i in r["items"]}
        self.assertTrue(st["delay"] and st["secrecy"])
        self.assertFalse(st["dispute"])
        self.assertIn("분쟁 해결·관할", r["missing"])
        with self.assertRaises(DmsError):
            self.d.clause_check(self.U["choi"], doc["id"])

    def test_version_summary_reports_amount_and_section_changes(self):
        doc = self.d.register(self.U["kim"], "v.md", CT1, folder_id=self.fid, doc_type="GEN", title="변경 요약")
        self.d.new_version(self.U["kim"], doc["id"], "v.md", CT2)
        r = self.d.version_summary(self.U["kim"], doc["id"], 1, 2)
        txt = " ".join(r["points"])
        self.assertIn("480,000,000원", txt)
        self.assertIn("500,000,000원", txt)
        self.assertIn("분쟁 해결", txt)
        self.assertIn("비밀 유지", txt)
        self.assertIn("2026-12-31", txt)

    def test_nl_parse(self):
        r = self.d.nl_parse(self.U["kim"], "내가 올린 승인된 계약서 최근 30일 #제주")
        f = r["filters"]
        self.assertEqual((f.get("mine"), f.get("status"), f.get("type"), f.get("tag")), (True, "approved", "CT", "제주"))
        self.assertIn("date_from", f)
        self.assertEqual(r["q"], "")
        r2 = self.d.nl_parse(self.U["kim"], "지체상금 조항 찾아줘")
        self.assertEqual(r2["q"], "지체상금 조항")
        self.assertIn("result", self.d.nl_search(self.U["kim"], "지체상금"))

    def test_draft_is_cited_and_permission_trimmed(self):
        self.d.register(self.U["kim"], "d.md", "# 인센티브 정책\n\n유치 인센티브는 행사 규모에 따라 지급한다. 초안근거토큰".encode(), folder_id=self.fid, doc_type="GEN", title="인센티브 정책")
        r = self.d.draft_from_evidence(self.U["kim"], "인센티브 정책 초안근거토큰")
        self.assertIn("출처:", r["text"])
        self.assertTrue(r["refs"])
        with self.assertRaises(DmsError):
            self.d.draft_from_evidence(self.U["choi"], "초안근거토큰 인센티브 정책 유치")
        with self.assertRaises(DmsError):
            self.d.draft_from_evidence(self.U["kim"], "")

    def test_trust_run_and_knowledge_gaps(self):
        with self.assertRaises(DmsError):
            self.d.trust_run(self.U["kim"])
        saved = self.app.cfg.pop("eval", None)
        self.assertFalse(self.d.trust_status(self.U["admin"])["configured"])
        golden = Path(__file__).resolve().parent.parent / "examples" / "demo" / "golden.json"
        self.app.cfg["eval"] = {"golden": str(golden), "daily": True}
        try:
            st = self.d.trust_run(self.U["admin"])
            self.assertEqual(len(st["runs"]), 1)
            self.assertEqual(st["latest"]["leaks"], 0)
            self.assertEqual(self.d.trust_maintenance(), 0)                           # once per day
        finally:
            self.app.cfg.pop("eval")
            if saved is not None:
                self.app.cfg["eval"] = saved
        self.app.engine.ask(self.U["kim"], "전혀관련없는질문xyzzy 가나다라")
        g = self.d.knowledge_gaps(self.U["admin"])
        self.assertIn("gaps", g)
        with self.assertRaises(DmsError):
            self.d.knowledge_gaps(self.U["kim"])


if __name__ == "__main__":
    unittest.main()
