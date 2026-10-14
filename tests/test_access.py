import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.errors import DmsError  # noqa: E402


class AccessAndFollow(test_platform.DemoCase):
    def setUp(self):
        self.d = self.app.dms
        self.U = self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]
        self.doc = self.d.register(self.U["kim"], "req.md", "# 신청대상\n\n접근신청유니크본문".encode(), folder_id=self.fid,
                                   entries=[{"type": "user", "id": "kim", "role": "manager"}])

    def err(self, code, fn, *a, **k):
        with self.assertRaises(DmsError) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.code, code)

    def req_for(self, uid):
        return next(r for r in self.d.list_access_requests(self.U["kim"])["to_decide"] if r["requester"] == uid and r["doc_id"] == self.doc["id"])

    # ---- requests ----
    def test_request_is_neutral_about_existence(self):
        real = self.d.request_access(self.U["choi"], self.doc["doc_no"], "viewer", "필요")
        missing = self.d.request_access(self.U["choi"], "ZZZ-9999-0000", "viewer", "필요")
        self.assertEqual(real, missing)                                        # identical answer: no existence oracle
        self.assertNotIn("doc_id", real)
        self.assertEqual(self.d.list_access_requests(self.U["choi"])["mine"][0]["title"], None)   # title stays hidden until approved

    def test_approve_grants_exactly_the_requested_role_and_reveals_the_document(self):
        self.err(404, self.d.get, self.U["choi"], self.doc["id"])
        self.d.request_access(self.U["choi"], str(self.doc["id"]), "viewer", "검토 필요")
        todo = self.d.list_access_requests(self.U["kim"])["to_decide"]
        req = next(r for r in todo if r["doc_id"] == self.doc["id"])
        self.assertEqual((req["requester"], req["role"]), ("choi", "viewer"))
        self.assertTrue([r for r in self.d.list_access_requests(self.U["park"])["to_decide"] if r["id"] == req["id"]] == [])    # not a manager
        self.err(403, self.d.decide_access, self.U["park"], req["id"], True)     # park can read it through the folder, but is no manager
        self.d.decide_access(self.U["kim"], req["id"], True, "승인")
        got = self.d.get(self.U["choi"], self.doc["id"])
        self.assertIn("read", got["perms"])
        self.assertNotIn("edit", got["perms"])
        mine = self.d.list_access_requests(self.U["choi"])["mine"][0]
        self.assertEqual((mine["status"], mine["doc_id"]), ("approved", self.doc["id"]))
        self.err(404, self.d.decide_access, self.U["kim"], req["id"], True)       # already decided

    def test_deny_keeps_document_hidden(self):
        self.d.request_access(self.U["jung"], self.doc["doc_no"], "editor")
        req = self.req_for("jung")
        self.d.decide_access(self.U["kim"], req["id"], False, "범위 밖")
        self.err(404, self.d.get, self.U["jung"], self.doc["id"])
        mine = self.d.list_access_requests(self.U["jung"])["mine"][0]
        self.assertEqual((mine["status"], mine["doc_id"], mine["note"]), ("denied", None, "범위 밖"))

    def test_upgrade_viewer_to_editor_and_no_downgrade(self):
        self.d.set_acl(self.U["kim"], self.doc["id"], entries=[{"type": "user", "id": "kim", "role": "manager"}, {"type": "user", "id": "park", "role": "viewer"}])
        self.d.request_access(self.U["park"], self.doc["doc_no"], "editor")
        req = self.req_for("park")
        self.d.decide_access(self.U["kim"], req["id"], True)
        self.assertIn("edit", self.d.get(self.U["park"], self.doc["id"])["perms"])
        self.assertEqual(self.d.request_access(self.U["park"], self.doc["doc_no"], "viewer")["message"], "이미 해당 권한으로 접근할 수 있는 문서입니다.")

    def test_denied_user_cannot_be_granted_and_limits(self):
        self.d.set_acl(self.U["kim"], self.doc["id"], entries=[{"type": "user", "id": "kim", "role": "manager"}, {"type": "deny", "id": "choi", "role": "viewer"}])
        self.d.request_access(self.U["choi"], self.doc["doc_no"], "viewer")
        req = self.req_for("choi")
        self.err(409, self.d.decide_access, self.U["kim"], req["id"], True)
        self.err(400, self.d.request_access, self.U["choi"], self.doc["doc_no"], "manager")
        self.err(400, self.d.request_access, self.U["choi"], "  ", "viewer")
        for n in range(25):
            try:
                self.d.request_access(self.U["lee"], f"NOPE-{n}", "viewer")
            except DmsError as e:
                self.assertEqual(e.code, 429)
        # requests for missing documents leave no rows, so the cap only counts real ones
        self.assertLessEqual(self.d.q1("SELECT COUNT(*) AS n FROM access_requests WHERE requester='lee'")["n"], 20)

    def test_admin_decides_but_cannot_read_title_unless_allowed(self):
        self.d.request_access(self.U["choi"], self.doc["doc_no"], "viewer")
        todo = self.d.list_access_requests(self.U["admin"])["to_decide"]
        item = next(r for r in todo if r["doc_id"] == self.doc["id"])
        self.assertIsNone(item["title"])                                         # admin manages ACLs but does not read documents

    # ---- follow ----
    def test_follow_notifies_only_while_the_follower_can_read(self):
        self.d.set_acl(self.U["kim"], self.doc["id"], entries=[{"type": "user", "id": "kim", "role": "manager"}, {"type": "user", "id": "park", "role": "viewer"}], inherit=False)
        self.assertEqual(self.d.set_follow(self.U["park"], self.doc["id"], True), {"following": True})
        self.assertTrue(self.d.get(self.U["park"], self.doc["id"])["following"])
        before = self.d.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id='park'")["n"]
        self.d.new_version(self.U["kim"], self.doc["id"], "req.md", "# 신청대상\n\n개정".encode(), "개정")
        after = self.d.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id='park' AND kind='follow_update'")["n"]
        self.assertGreater(self.d.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id='park'")["n"], before)
        self.assertEqual(after, 1)
        self.assertEqual(self.d.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id='kim' AND kind='follow_update'")["n"], 0)    # not told about own change
        # access removed -> no more notifications about this document
        self.d.set_acl(self.U["kim"], self.doc["id"], entries=[{"type": "user", "id": "kim", "role": "manager"}], inherit=False)
        self.d.new_version(self.U["kim"], self.doc["id"], "req.md", "# 신청대상\n\n두번째 개정".encode(), "2")
        self.assertEqual(self.d.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id='park' AND kind='follow_update'")["n"], 1 + 0)
        self.assertEqual(self.d.my_follows(self.U["park"]), [])                  # and it disappears from the list
        self.err(404, self.d.set_follow, self.U["choi"], self.doc["id"], True)  # cannot follow what you cannot read

    # ---- activity ----
    def test_activity_feed_never_contains_hidden_documents(self):
        hidden = self.d.register(self.U["park"], "h.md", "# 재무비밀활동\n\n내용".encode(), folder_id=self.d.q1("SELECT id FROM folders WHERE name='경영지원팀'")["id"])
        feed_park = self.d.activity(self.U["park"])
        feed_choi = self.d.activity(self.U["choi"])
        self.assertTrue([e for e in feed_park if e["doc_id"] == hidden["id"]])
        self.assertFalse([e for e in feed_choi if e["doc_id"] == hidden["id"]])
        self.assertFalse([e for e in feed_park if e["action"] == "downloaded"])


if __name__ == "__main__":
    unittest.main()


class AskOneDocument(test_platform.DemoCase):
    def test_scoped_answers_come_only_from_that_document(self):
        d, U = self.app.dms, self.app.users
        listed = {r["source"]: r["id"] for r in d.q("SELECT id,source FROM dms_docs")}
        contract = listed["contracts/제주관광공사_MICE위크_대관계약요약.md"]
        out = d.ask_doc(U["kim"], contract, "지체상금 조항")
        self.assertTrue(out["evidence"])
        docs = [e for e in out["evidence"] if e["kind"] == "doc"]
        self.assertTrue(docs and all(e["ref"].startswith("contracts/제주관광공사_MICE위크") for e in docs))      # never another document
        self.assertFalse([e for e in out["evidence"] if e["kind"] == "metric"])                                # no company-wide aggregates
        self.assertEqual(out["doc"]["id"], contract)
        # the same question company-wide finds more than this one document
        wide = self.app.engine.ask(U["kim"], "제주관광공사 지체상금 조항")
        self.assertGreater(len({e.ref.split("#")[0] for e in wide.evidence}), 1)

    def test_scope_cannot_widen_permissions(self):
        d, U = self.app.dms, self.app.users
        hr = d.q1("SELECT id FROM dms_docs WHERE source LIKE 'hr/%'")["id"]
        with self.assertRaises(Exception) as cm:
            d.ask_doc(U["kim"], hr, "보너스 지급 대상")
        self.assertEqual(cm.exception.code, 404)                                  # hidden stays hidden
        # direct engine call with a source the user cannot read yields nothing, not the document
        ans = self.app.engine.ask(U["kim"], "보너스 지급 대상", only_source="hr/2024_성과평가_및_보상.md")
        self.assertTrue(ans.refused)
        self.assertTrue(d.ask_doc(U["lee"], hr, "보너스 지급 대상")["evidence"])
        self.assertEqual(d.ask_doc(U["kim"], d.q1("SELECT id FROM dms_docs WHERE source LIKE 'contracts/제주관광%'")["id"], "오늘 점심 메뉴")["refused"], True)

    def test_empty_question(self):
        d = self.app.dms
        with self.assertRaises(Exception) as cm:
            d.ask_doc(self.app.users["kim"], d.q1("SELECT id FROM dms_docs LIMIT 1")["id"], "  ")
        self.assertEqual(cm.exception.code, 400)


class AdminViews(test_platform.DemoCase):
    def test_admin_only_and_no_email_addresses(self):
        d, U = self.app.dms, self.app.users
        for fn in (d.admin_users, d.system_status):
            with self.assertRaises(Exception) as cm:
                fn(U["kim"])
            self.assertEqual(cm.exception.code, 403)
        users = d.admin_users(U["admin"])
        self.assertEqual({u["id"] for u in users["users"]}, set(U))
        self.assertNotIn("@", str(users))                                       # addresses are never returned by any API
        kim = next(u for u in users["users"] if u["id"] == "kim")
        self.assertEqual((kim["dept"], kim["has_email"], "slack" in kim["external_ids"]), ("mice", True, True))
        st = d.system_status(U["admin"])
        self.assertTrue(st["audit"]["chain_ok"])
        self.assertEqual(st["structured_db"]["tables"], ["contracts", "customers", "invoices"])
        self.assertGreater(st["index"]["chunks"], 0)
        self.assertNotIn("secret", str(st).lower().replace("signing_secret_env", ""))
