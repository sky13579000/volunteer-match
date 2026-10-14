import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai import automation, edm  # noqa: E402
from edm_ai.edm import EdmError  # noqa: E402


class Automation(unittest.TestCase):
    def test_korean_amounts(self):
        cases = {"계약금액은 4억 8천만원이다": 480000000, "480,000,000원": 480000000, "1.2억원": 120000000,
                 "1억 5,000만원": 150000000, "3천5백만원": 35000000, "12,500원": 12500, "2조 1억원": 2000100000000,
                 "만원": 10000, "1억 2천3백만 4천원": 123004000}
        for text, want in cases.items():
            self.assertEqual(automation.parse_krw(text), [want], text)
        self.assertEqual(automation.parse_krw("인원 30명 참석"), [])

    def test_dates_and_fields(self):
        self.assertEqual(automation.parse_dates("납기는 2025년 4월 30일, 또는 2025-03-31"), ["2025-04-30", "2025-03-31"])
        f = automation.extract_fields("PRJ-2031 계약(CT-2031-003). 계약금액은 1억 5천만원. 납기는 2025년 4월 30일이다.")
        self.assertEqual((f["contract_no"], f["project"], f["amount"], f["due_date"]),
                         ("CT-2031-003", "PRJ-2031", 150000000, "2025-04-30"))

    def test_classify_and_summary(self):
        self.assertEqual(automation.classify("회의록", "안건 결정 사항 참석자"), "회의록")
        self.assertEqual(automation.classify("x", "아무 관련 없는 문장"), "기타")
        self.assertTrue(automation.summarize("# 제목\n\n첫 번째 문장은 충분히 길게 씁니다. 두 번째 문장도 충분히 깁니다. 세 번째 문장.").startswith("첫 번째"))


class EdmCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.edm = cls.app.edm
        cls.u = cls.app.users

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def up(self, uid, name, text="# 제목\n\n본문 문장입니다. 한빛전자 PRJ-2024 관련 메모.", acl=None):
        return self.edm.upload(self.u[uid], name, text.encode(), acl)

    def titles(self, uid, q=None):
        return {d["source"] for d in self.edm.list(self.u[uid], q)}

    # ---- visibility ----
    def test_list_is_acl_filtered_and_acl_hidden_from_non_editors(self):
        sales = {d["source"]: d for d in self.edm.list(self.u["kim"])}
        self.assertNotIn("hr/2024_성과평가_및_보상.md", sales)
        self.assertNotIn("acl", sales["contracts/한빛전자_MES_계약요약.md"])      # kim is not owner/admin
        self.assertIn("hr/2024_성과평가_및_보상.md", self.titles("lee"))

    def test_hidden_document_is_404_not_403(self):
        hr = next(d for d in self.edm.list(self.u["lee"]) if d["source"].startswith("hr/"))
        for fn in (lambda: self.edm.get(self.u["kim"], hr["id"]), lambda: self.edm.delete(self.u["kim"], hr["id"]),
                   lambda: self.edm.set_acl(self.u["kim"], hr["id"], {"level": 0})):
            with self.assertRaises(EdmError) as cm:
                fn()
            self.assertEqual(cm.exception.code, 404)

    def test_search_in_list_respects_acl(self):
        self.assertFalse([s for s in self.titles("kim", "성과 보너스") if s.startswith("hr/")])
        self.assertTrue([s for s in self.titles("lee", "성과 보너스") if s.startswith("hr/")])

    def test_detail_links_db_rows_with_masking(self):
        d = next(x for x in self.edm.list(self.u["kim"]) if x["source"].startswith("contracts/한빛"))
        det = self.edm.get(self.u["kim"], d["id"])
        row = next(r for r in det["linked_rows"] if r["table"] == "contracts")
        self.assertIn("margin_rate", row["masked"])
        self.assertEqual(det["analysis"]["category"], "계약서")
        self.assertTrue(all(c["status"] == "ok" for c in det["analysis"]["checks"]))
        self.assertTrue(det["analysis"]["checks"])

    # ---- upload ----
    def test_upload_defaults_to_uploaders_department_and_is_searchable(self):
        d = self.up("kim", "kim_memo1.md", "# 메모\n\n납기 협의 일정 메모입니다. 김영업이 작성했습니다 유니크토큰알파.")
        self.assertEqual(d["acl"], {"roles": [], "depts": ["sales"], "users": [], "deny_users": [], "level": 2})
        self.assertIn("uploads/kim_memo1.md", self.titles("kim", "유니크토큰알파"))
        self.assertNotIn("uploads/kim_memo1.md", self.titles("choi"))
        ans = self.app.engine.ask(self.u["kim"], "유니크토큰알파 메모")
        self.assertIn("uploads/kim_memo1.md", {e.ref.split("#")[0] for e in ans.evidence})
        self.assertFalse(self.app.engine.ask(self.u["choi"], "유니크토큰알파 메모").evidence and
                         any("kim_memo1" in e.ref for e in self.app.engine.ask(self.u["choi"], "유니크토큰알파 메모").evidence))

    def test_upload_acl_guards(self):
        with self.assertRaises(EdmError) as cm:
            self.up("kim", "g1.md", acl={"level": 3})
        self.assertIn("보안등급", cm.exception.msg)
        with self.assertRaises(EdmError) as cm:
            self.up("kim", "g2.md", acl={"roles": ["hr"], "level": 2})          # kim could not read it himself
        self.assertEqual(cm.exception.code, 400)
        self.up("admin", "g3.md", acl={"roles": ["hr"], "level": 3})            # admin may
        with self.assertRaises(EdmError):
            self.up("kim", "g4.md", acl={"level": "abc"})

    def test_upload_filename_and_type_validation(self):
        d = self.up("kim", "../../evil name.md")
        self.assertEqual(d["source"], "uploads/evil name.md")                    # traversal stripped
        self.assertFalse((Path(self.tmp.name) / "evil name.md").exists())
        for bad in ("run.exe", ".hidden.md", "", "a" * 200 + ".md"):
            with self.assertRaises(EdmError, msg=bad):
                self.up("kim", bad)
        with self.assertRaises(EdmError) as cm:
            self.edm.upload(self.u["kim"], "empty.md", b"", None)
        self.assertEqual(cm.exception.code, 400)

    def test_upload_size_limit(self):
        old, edm.MAX_UPLOAD = edm.MAX_UPLOAD, 10
        try:
            with self.assertRaises(EdmError) as cm:
                self.up("kim", "big.md", "x" * 50)
            self.assertEqual(cm.exception.code, 413)
        finally:
            edm.MAX_UPLOAD = old

    def test_corrupt_office_file_is_rejected_and_not_left_behind(self):
        with self.assertRaises(EdmError) as cm:
            self.edm.upload(self.u["kim"], "broken.docx", b"not a zip", None)
        self.assertEqual(cm.exception.code, 422)
        self.assertFalse((Path(self.tmp.name) / "docs" / "uploads" / "broken.docx").exists())
        self.assertNotIn("uploads/broken.docx", self.titles("admin"))

    def test_name_collision_between_users(self):
        self.up("kim", "shared_name.md")
        with self.assertRaises(EdmError) as cm:
            self.up("jung", "shared_name.md")
        self.assertEqual(cm.exception.code, 409)
        self.up("kim", "shared_name.md", "# 새 버전\n\n수정된 본문 내용입니다 두번째.")        # owner may overwrite
        self.up("admin", "shared_name.md", "# 관리자 버전\n\n관리자가 수정한 본문입니다.", {"level": 0})

    # ---- ACL edit / delete ----
    def test_owner_changes_acl_and_visibility_follows(self):
        d = self.up("kim", "acl_change.md")
        self.assertIn("uploads/acl_change.md", self.titles("kim"))
        self.assertNotIn("uploads/acl_change.md", self.titles("jung"))
        self.edm.set_acl(self.u["kim"], d["id"], {"depts": ["sales", "consulting"], "level": 2})
        did = next(x["id"] for x in self.edm.list(self.u["jung"]) if x["source"] == "uploads/acl_change.md")
        self.assertTrue(self.edm.get(self.u["jung"], did))
        with self.assertRaises(EdmError) as cm:                                   # jung can read but not edit
            self.edm.set_acl(self.u["jung"], did, {"level": 0})
        self.assertEqual(cm.exception.code, 403)
        self.assertTrue(any(h["action"] == "acl_changed" for h in self.edm.get(self.u["kim"], did)["history"]))

    def test_cannot_lock_yourself_out_via_acl_change(self):
        d = self.up("kim", "lockout.md")
        with self.assertRaises(EdmError):
            self.edm.set_acl(self.u["kim"], d["id"], {"depts": ["finance"], "level": 2})

    def test_delete_rules(self):
        d = self.up("kim", "to_delete.md")
        with self.assertRaises(EdmError) as cm:
            self.edm.delete(self.u["park"], d["id"]) if False else self.edm.delete(self.u["jung"], d["id"])
        self.assertEqual(cm.exception.code, 404)                                   # jung cannot even see it
        self.edm.delete(self.u["kim"], d["id"])
        self.assertNotIn("uploads/to_delete.md", self.titles("admin"))
        self.assertFalse((Path(self.tmp.name) / "docs" / "uploads" / "to_delete.md").exists())
        crawled = next(x for x in self.edm.list(self.u["admin"]) if x["source"].startswith("contracts/한빛"))
        with self.assertRaises(EdmError) as cm:
            self.edm.delete(self.u["admin"], crawled["id"])
        self.assertEqual(cm.exception.code, 409)

    def test_admin_can_edit_any_acl_override_survives_reingest(self):
        d = next(x for x in self.edm.list(self.u["admin"]) if x["source"].startswith("drawings/"))
        self.edm.set_acl(self.u["admin"], d["id"], {"roles": ["hr"], "level": 3})
        self.assertNotIn("drawings/대성정밀_도면검토_PRJ-2025.md", self.titles("choi"))
        self.app.ingest()                                                         # manifest says "open"; override must win
        self.assertNotIn("drawings/대성정밀_도면검토_PRJ-2025.md", self.titles("choi"))
        self.edm.set_acl(self.u["admin"], next(x["id"] for x in self.edm.list(self.u["admin"])
                                               if x["source"].startswith("drawings/")), {"level": 1})
        self.assertIn("drawings/대성정밀_도면검토_PRJ-2025.md", self.titles("choi"))

    def test_admin_can_manage_but_not_read_a_document_outside_their_acl(self):
        d = self.up("admin", "finance_only.md", "# 재무\n\n재무팀 전용 메모입니다 재무전용토큰.", {"depts": ["finance"], "level": 2})
        self.assertFalse(d["readable"])
        self.assertNotIn("text", d)                                                 # summary only, no content
        listed = next(x for x in self.edm.list(self.u["admin"]) if x["source"] == "uploads/finance_only.md")
        self.assertFalse(listed["readable"])
        self.assertEqual(listed["entities"], [])
        with self.assertRaises(EdmError) as cm:
            self.edm.get(self.u["admin"], d["id"])                                  # reading is still denied
        self.assertEqual(cm.exception.code, 404)
        self.assertNotIn("uploads/finance_only.md", self.titles("admin", "재무전용토큰"))   # not searchable by admin either
        self.assertNotIn("uploads/finance_only.md", self.titles("kim"))
        self.edm.delete(self.u["admin"], d["id"])                                   # but administering works
        self.assertNotIn("uploads/finance_only.md", {x["source"] for x in self.edm.list(self.u["admin"])})

    def test_detail_redacts_pii_unless_role_allows(self):
        txt = "# 연락\n\n담당자 연락처는 010-1234-5678 이고 주민번호는 900101-1234567 입니다 PIItest."
        d = self.up("admin", "pii.md", txt, {"level": 0})
        self.assertNotIn("1234", self.edm.get(self.u["kim"], d["id"])["text"])
        self.assertIn("[전화번호]", self.edm.get(self.u["kim"], d["id"])["text"])
        self.assertIn("010-1234-5678", self.edm.get(self.u["lee"], d["id"])["text"])   # hr may reveal

    # ---- alerts (automation) ----
    def test_alerts_follow_document_and_column_permissions(self):
        jung = {a["source"] for a in self.edm.alerts(self.u["jung"])}
        self.assertIn("contracts/누리건설_현장문서_계약서.md", jung)
        self.assertEqual(self.edm.alerts(self.u["kim"]), [])                      # cannot read that document
        self.assertEqual(self.edm.alerts(self.u["choi"]), [])
        f = next(a for a in self.edm.alerts(self.u["park"]) if a["source"].startswith("contracts/누리"))["findings"]
        self.assertEqual({x["field"] for x in f}, {"계약금액", "납기"})

    def test_masked_amount_column_is_never_compared(self):
        # level-1 user who can read the doc but not the amount column: no comparison, hence no leak via a mismatch message
        doc_text = "# 계약\n\nCT-2031-003 계약금액은 9억원이다. 납기는 2025년 4월 30일이다."
        self.up("admin", "lvl1_contract.md", doc_text, {"level": 0, "depts": ["external"]})
        outs = [a for a in self.edm.alerts(self.u["choi"]) if a["source"] == "uploads/lvl1_contract.md"]
        for a in outs:
            self.assertFalse([f for f in a["findings"] if f["field"] == "계약금액"])
            self.assertNotIn("120,000,000", json.dumps(a, ensure_ascii=False))

    def test_uploaded_bad_contract_raises_alert_for_finance(self):
        self.up("admin", "bad_contract.md", "# 계약\n\nCT-2024-001 계약금액은 5억원이다.", {"depts": ["finance"], "level": 2})
        a = next(x for x in self.edm.alerts(self.u["park"]) if x["source"] == "uploads/bad_contract.md")
        self.assertIn("500,000,000", a["findings"][0]["message"])
        self.assertIn("480,000,000", a["findings"][0]["message"])


class EdmHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer

        from edm_ai.server import make_handler
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.app.cfg["auth"], cls.app.cfg["rate_limit_per_min"] = {"mode": "header"}, 1000
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, uid=None, body=None, raw=None):
        import urllib.error
        import urllib.request
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data, {"X-User-Id": uid} if uid else {}, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_full_lifecycle_over_http(self):
        up = {"filename": "http_doc.md", "content_b64": base64.b64encode("# HTTP\n\n업로드 경로 확인 본문입니다 httpmarker.".encode()).decode()}
        self.assertEqual(self.call("POST", "/api/docs", None, up)[0], 401)
        code, d = self.call("POST", "/api/docs", "kim", up)
        self.assertEqual(code, 200)
        did = d["id"]
        self.assertTrue(any(x["id"] == did for x in self.call("GET", "/api/docs?q=httpmarker", "kim")[1]))
        self.assertEqual(self.call("GET", "/api/docs/%d" % did, "choi")[0], 404)
        code, d2 = self.call("PUT", "/api/docs/%d/acl" % did, "kim", {"acl": {"depts": ["sales", "external"], "level": 1}})
        self.assertEqual(code, 200)
        self.assertEqual(self.call("GET", "/api/docs/%d" % did, "choi")[0], 200)
        self.assertEqual(self.call("DELETE", "/api/docs/%d" % did, "choi")[0], 403)
        self.assertEqual(self.call("DELETE", "/api/docs/%d" % did, "kim")[0], 200)
        self.assertEqual(self.call("GET", "/api/docs/%d" % did, "kim")[0], 404)

    def test_bad_inputs(self):
        self.assertEqual(self.call("POST", "/api/docs", "kim", {"filename": "x.md", "content_b64": "###"})[0], 400)
        self.assertEqual(self.call("POST", "/api/docs", "kim", raw=b"not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/docs", "kim", {"filename": "x.exe", "content_b64": "YQ=="})[0], 400)
        self.assertEqual(self.call("GET", "/api/docs/99999", "kim")[0], 404)
        self.assertEqual(self.call("PUT", "/api/docs/1", "kim", {})[0], 404)       # only /acl is a PUT route

    def test_alerts_endpoint(self):
        code, a = self.call("GET", "/api/alerts", "jung")
        self.assertEqual(code, 200)
        self.assertTrue(a)
        self.assertEqual(self.call("GET", "/api/alerts", "kim")[1], [])


if __name__ == "__main__":
    unittest.main()
