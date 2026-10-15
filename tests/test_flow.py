import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


class Flow(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]

    def err(self, code, fn, *a, **k):
        with self.assertRaises(DmsError) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.code, code)

    def test_custom_fields_validated_and_exported(self):
        self.err(403, self.d.set_type_fields, self.U["kim"], "GEN", [])
        self.err(400, self.d.set_type_fields, self.U["admin"], "GEN", [{"key": "1bad", "label": "x"}])
        self.err(400, self.d.set_type_fields, self.U["admin"], "GEN", [{"key": "kind", "label": "구분", "type": "select"}])
        self.d.set_type_fields(self.U["admin"], "GEN", [{"key": "partner", "label": "거래처", "required": True}, {"key": "amount", "label": "금액", "type": "number"},
                                                         {"key": "due", "label": "기한", "type": "date"}, {"key": "kind", "label": "구분", "type": "select", "options": ["A", "B"]}])
        try:
            self.err(400, self.d.register, self.U["kim"], "f.md", b"# f\n\nx1", folder_id=self.fid, doc_type="GEN")                          # required missing
            self.err(400, self.d.register, self.U["kim"], "f.md", b"# f\n\nx2", folder_id=self.fid, doc_type="GEN", props={"partner": "P", "amount": "abc"})
            self.err(400, self.d.register, self.U["kim"], "f.md", b"# f\n\nx3", folder_id=self.fid, doc_type="GEN", props={"partner": "P", "kind": "Z"})
            doc = self.d.register(self.U["kim"], "f.md", b"# f\n\nflowfield4", folder_id=self.fid, doc_type="GEN", title="항목 문서", props={"partner": "제주관광공사", "amount": "1,000", "due": "2026-12-01", "kind": "A"})
            self.err(400, self.d.update_meta, self.U["kim"], doc["id"], props={"partner": "P", "due": "내일"})
            self.d.update_meta(self.U["kim"], doc["id"], props={"partner": "P2", "amount": "5"})
            csv_ = self.d.docs_csv(self.U["kim"], type="GEN", q="flowfield4")
            self.assertIn("거래처", csv_)
            self.assertIn("항목 문서", csv_)
            self.assertIn("P2", csv_)
            self.assertEqual([f["key"] for f in next(t for t in self.d.list_doc_types(self.U["admin"]) if t["code"] == "GEN")["fields"]], ["partner", "amount", "due", "kind"])
        finally:
            self.d.set_type_fields(self.U["admin"], "GEN", [])

    def test_csv_hides_unreadable_and_neutralises_formulas(self):
        self.d.register(self.U["kim"], "s.md", b"# s\n\nsecretcsvtoken", folder_id=self.fid, doc_type="GEN", title="=HYPERLINK(1)", entries=[{"type": "user", "id": "kim", "role": "manager"}], inherit=False)
        mine = self.d.docs_csv(self.U["kim"], q="secretcsvtoken")
        self.assertIn("HYPERLINK", mine)
        self.assertNotIn("\n=HYPERLINK", mine)
        self.assertNotIn("HYPERLINK", self.d.docs_csv(self.U["choi"], q="secretcsvtoken"))

    def test_templates_cc_and_notifications(self):
        t = self.d.save_approval_template(self.U["kim"], {"name": "계약 결재선", "approvers": ["park"], "cc": ["lee"], "mode": "sequential"})
        self.assertEqual(self.d.approval_templates(self.U["kim"])[0]["name"], "계약 결재선")
        self.assertEqual(self.d.approval_templates(self.U["choi"]), [])                                 # private
        self.err(400, self.d.save_approval_template, self.U["kim"], {"name": "x", "approvers": ["nobody"]})
        self.err(404, self.d.delete_approval_template, self.U["choi"], t["id"])
        doc = self.d.register(self.U["kim"], "c.md", b"# c\n\nccflow", folder_id=self.fid, doc_type="CT", title="참조 결재")
        self.err(400, self.d.submit, self.U["kim"], doc["id"], ["park"], cc=["nobody"])
        self.d.submit(self.U["kim"], doc["id"], ["park"], cc=["lee"])
        self.assertIn("cc_notice", [n["kind"] for n in self.d.notifications(self.U["lee"])["items"]])
        self.d.approve(self.U["park"], doc["id"], "ok")
        self.assertEqual([n["kind"] for n in self.d.notifications(self.U["lee"])["items"]].count("cc_notice"), 2)
        self.d.delete_approval_template(self.U["kim"], t["id"])

    def test_absence_hands_over_active_steps_to_deputy(self):
        doc = self.d.register(self.U["kim"], "a.md", b"# a\n\nabsenceflow", folder_id=self.fid, doc_type="CT", title="대결 시험")
        self.d.submit(self.U["kim"], doc["id"], ["park"])
        self.err(400, self.d.set_absence, self.U["park"], "park", date.today().isoformat(), date.today().isoformat())
        self.err(400, self.d.set_absence, self.U["park"], "lee", "2026-02-01", "2026-01-01")
        r = self.d.set_absence(self.U["park"], "lee", date.today().isoformat(), (date.today() + timedelta(days=3)).isoformat(), "휴가")
        self.assertGreaterEqual(r["reassigned"], 1)
        self.err(404, self.d.approve, self.U["park"], doc["id"])                                        # absent approver no longer holds the step
        self.d.approve(self.U["lee"], doc["id"], "대결 승인")
        st = self.d.workflow_state(self.U["kim"], self.d.row(doc["id"]))
        self.assertEqual((st["status"], st["steps"][0]["delegated_from"]), ("approved", "park"))
        self.d.clear_absence(self.U["park"])
        self.assertIsNone(self.d.my_absence(self.U["park"]))


if __name__ == "__main__":
    unittest.main()
