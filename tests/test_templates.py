import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.errors import DmsError  # noqa: E402


class TemplatesAndEditing(test_platform.DemoCase):
    def err(self, code, fn, *a, **k):
        with self.assertRaises(DmsError) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.code, code)

    def setUp(self):
        self.d, self.kim = self.app.dms, self.app.users["kim"]
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]

    def test_list_and_render(self):
        ids = {t["id"] for t in self.d.templates(self.kim)}
        self.assertTrue({"rental-contract", "meeting-minutes", "event-report"} <= ids)
        r = self.d.render_template(self.kim, "rental-contract", {"organizer": "제주관광공사", "amount": "4억 8천만원", "event": "MICE 위크"}, "대관 계약서 초안")
        self.assertEqual((r["doc_type"], r["filename"]), ("CT", "대관_계약서_초안.md"))
        self.assertIn("계약 상대방: 제주관광공사", r["text"])
        self.assertIn("작성자: 김유치 (mice)", r["text"])
        self.assertNotIn("{{organizer}}", r["text"])
        self.assertNotIn("{{", self.d.render_template(self.kim, "meeting-minutes", {}, "x")["text"])      # unfilled fields become empty, never raw markers
        self.err(404, self.d.render_template, self.kim, "nope", {})
        self.err(400, self.d.render_template, self.kim, "meeting-minutes", ["x"])

    def test_config_can_add_and_override_templates(self):
        self.app.cfg["templates"] = [{"id": "custom", "name": "사내 양식", "doc_type": "GEN", "fields": [{"key": "a", "label": "A"}], "body": "# {{title}}\n{{a}}"}]
        try:
            self.assertIn("custom", {t["id"] for t in self.d.templates(self.kim)})
            self.assertEqual(self.d.render_template(self.kim, "custom", {"a": "값"}, "T")["text"], "# T\n값")
        finally:
            del self.app.cfg["templates"]

    def test_edit_creates_new_version_and_follows_the_workflow(self):
        r = self.d.render_template(self.kim, "meeting-minutes", {"agenda": "초안"}, "편집시험")
        doc = self.d.register(self.kim, r["filename"], r["text"].encode(), folder_id=self.fid, doc_type=r["doc_type"], tags=r["tags"])
        info = self.d.read_text(self.kim, doc["id"])
        self.assertEqual((info["ver"], info["filename"]), (1, "편집시험.md"))
        out = self.d.edit_text(self.kim, doc["id"], info["text"] + "\n\n## 추가\n편집추가문장유니크", "문장 추가")
        self.assertEqual(self.d.row(doc["id"])["current_ver"], 2)
        self.assertIn("편집추가문장유니크", self.d.read_text(self.kim, doc["id"])["text"])
        self.assertTrue(any(h.ref.startswith("dms/") for h in self.app.engine.ask(self.kim, "편집추가문장유니크").evidence))      # re-indexed
        self.assertEqual(out["id"], doc["id"])
        self.err(409, self.d.edit_text, self.kim, doc["id"], self.d.read_text(self.kim, doc["id"])["text"])            # unchanged
        self.err(400, self.d.edit_text, self.kim, doc["id"], "   ")

    def test_edit_permissions_and_limits(self):
        doc = self.d.register(self.kim, "p.md", "# 비공개편집\n\n본문".encode(), folder_id=self.fid)
        self.err(404, self.d.read_text, self.app.users["choi"], doc["id"])             # hidden stays hidden
        self.err(404, self.d.edit_text, self.app.users["choi"], doc["id"], "x")
        csv = self.d.register(self.kim, "t.csv", "a,b\n1,2".encode(), folder_id=self.fid)
        self.err(415, self.d.read_text, self.kim, csv["id"])
        ext = self.d.q1("SELECT id FROM dms_docs WHERE origin='crawled' LIMIT 1")["id"]
        self.err(403, self.d.read_text, self.app.users["admin"], ext)             # admin manages but does not read/edit
        self.err(400, self.d.edit_text, self.kim, doc["id"], "가" * 700_000)

    def test_edit_blocked_while_checked_out_by_someone_else_or_in_review(self):
        doc = self.d.register(self.kim, "l.md", "# 잠금\n\n본문".encode(), folder_id=self.fid, doc_type="CT",
                              entries=[{"type": "user", "id": "kim", "role": "manager"}, {"type": "user", "id": "park", "role": "editor"}])
        self.d.checkout(self.kim, doc["id"])
        park = self.app.users["park"]
        self.err(409, self.d.edit_text, park, doc["id"], "# 잠금\n\n다른 사람 편집")
        self.d.cancel_checkout(self.kim, doc["id"])
        self.d.submit(self.kim, doc["id"], ["park"])
        self.err(409, self.d.edit_text, self.kim, doc["id"], "# 잠금\n\n결재중 편집")


if __name__ == "__main__":
    unittest.main()
