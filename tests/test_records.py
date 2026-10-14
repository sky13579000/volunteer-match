import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.errors import DmsError  # noqa: E402

PII = "# 명단\n\n홍길동 900101-1234567 연락처 010-1234-5678".encode()


class Base(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]
        self.app.cfg.pop("dlp", None)
        self.app.cfg["dlp"] = {"mode": "warn"}

    def err(self, code, fn, *a, **k):
        with self.assertRaises(DmsError) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.code, code)
        return cm.exception


class Privacy(Base):
    def reg(self, uid="kim", data=PII, **kw):
        return self.d.register(self.U[uid], kw.pop("name", "p.md"), data, folder_id=self.fid, **kw)

    def test_warn_records_counts_notifies_and_never_stores_values(self):
        doc = self.reg()
        info = doc["privacy"]
        self.assertEqual({f["kind"]: f["count"] for f in info["findings"]}, {"rrn": 1, "phone": 1})
        self.assertNotIn("900101", json.dumps(info))
        self.assertNotIn("900101", json.dumps(self.d.q("SELECT * FROM dlp_findings")))
        kinds = [n["kind"] for n in self.d.notifications(self.U["kim"])["items"]]
        self.assertIn("privacy_alert", kinds)
        self.assertIn("privacy_alert", [n["kind"] for n in self.d.notifications(self.U["rec"])["items"]])      # records staff are told, with counts only
        self.assertNotIn("900101", json.dumps(self.d.notifications(self.U["rec"])))
        self.assertEqual(self.d.row(doc["id"])["level"], 0)                                                  # warn mode changes nothing else

    def test_findings_are_visible_only_to_people_who_can_read_the_document(self):
        doc = self.reg(entries=[{"type": "user", "id": "kim", "role": "manager"}], inherit=False)
        self.assertIsNone(self.d.privacy_of(self.U["choi"], self.d.row(doc["id"])))
        self.assertIsNone(self.d.privacy_of(self.U["admin"], self.d.row(doc["id"])))                         # admin manages, does not read

    def test_block_mode_refuses_and_leaves_nothing_behind(self):
        self.app.cfg["dlp"] = {"mode": "block"}
        before = self.d.q1("SELECT COUNT(*) AS n FROM dms_docs")["n"]
        e = self.err(422, self.reg)
        self.assertIn("주민등록번호 1건", e.msg)
        self.assertNotIn("900101", e.msg)
        self.assertEqual(self.d.q1("SELECT COUNT(*) AS n FROM dms_docs")["n"], before)
        self.assertTrue(self.reg(data="# 평범\n\n전화 010-1234-5678".encode())["id"])                        # phone alone is not a blocked kind
        self.assertTrue(self.reg("lee")["id"] if self.d.folder_role(self.U["lee"], self.fid) >= 2 else True)   # exempt role (hr) is allowed to hold such data

    def test_block_mode_applies_to_new_versions_and_online_edits(self):
        self.app.cfg["dlp"] = {"mode": "block"}
        doc = self.reg(data="# 안전\n\n본문".encode())
        self.err(422, self.d.new_version, self.U["kim"], doc["id"], "p.md", PII)
        self.err(422, self.d.edit_text, self.U["kim"], doc["id"], PII.decode())
        self.assertEqual(self.d.row(doc["id"])["current_ver"], 1)

    def test_raise_level_mode_quarantines_the_document(self):
        self.app.cfg["dlp"] = {"mode": "raise_level", "raise_to": 3, "no_download": True}
        doc = self.reg(entries=[{"type": "user", "id": "kim", "role": "manager"}, {"type": "user", "id": "park", "role": "viewer"}], inherit=False)
        row = self.d.row(doc["id"])
        self.assertEqual(row["level"], 3)
        self.assertEqual(row["props"].get("no_download"), "1")
        self.assertIn("read", self.d.get(self.U["park"], doc["id"])["perms"])                              # L3 reader keeps access ...
        self.assertNotIn("read", self.d.get(self.U["kim"], doc["id"])["perms"])                            # below L3 even the author cannot read ...
        self.assertIn("acl", self.d.get(self.U["kim"], doc["id"])["perms"])                                # ... but still manages it (can ask for the level to be reviewed)
        self.err(404, self.d.get, self.U["lee"], doc["id"])                                                # everybody below L3 is out
        self.assertIn("dlp_protected", [h["action"] for h in self.d.store.history(row["source"])])

    def test_exempt_role_is_not_quarantined(self):
        self.app.cfg["dlp"] = {"mode": "raise_level", "exempt_roles": ["hr"]}
        hrf = self.d.q1("SELECT id FROM folders WHERE name='인사팀'")["id"]
        doc = self.d.register(self.U["lee"], "hr.md", PII, folder_id=hrf)
        self.assertEqual(self.d.row(doc["id"])["level"], 0)
        self.assertTrue(doc["privacy"])                                                                    # still recorded

    def test_off_mode(self):
        self.app.cfg["dlp"] = {"mode": "off"}
        self.assertIsNone(self.reg()["privacy"])

    def test_overview_scan_and_roles(self):
        doc = self.reg(entries=[{"type": "user", "id": "kim", "role": "manager"}], inherit=False)
        self.err(403, self.d.privacy_overview, self.U["kim"])
        ov = self.d.privacy_overview(self.U["rec"])
        item = next(i for i in ov["items"] if i["doc_id"] == doc["id"])
        self.assertIsNone(item["title"])                                                                   # records staff do not read hidden documents
        self.assertTrue(item["high"])
        self.assertIn("rrn", {t["kind"] for t in ov["totals"]})
        res = self.d.privacy_scan_all(self.U["admin"])
        self.assertGreater(res["scanned"], 10)
        self.assertGreaterEqual(res["with_findings"], 1)
        self.err(403, self.d.privacy_scan_all, self.U["kim"])


class Classification(Base):
    def test_save_validate_and_import_are_atomic(self):
        rec = self.U["rec"]
        c = self.d.save_classification(rec, {"code": "T-1", "name": "시험 분류", "retention_years": 7})
        self.assertEqual((c["code"], c["retention_years"]), ("T-1", 7))
        for bad in ({"code": "bad code!", "name": "x"}, {"code": "T-2", "name": ""}, {"code": "T-2", "name": "x", "retention_years": 150},
                    {"code": "T-2", "name": "x", "retention_years": "abc"}, {"code": "T-2", "name": "x", "parent": "NOPE"}):
            self.err(400, self.d.save_classification, rec, bad)
        self.err(403, self.d.save_classification, self.U["kim"], {"code": "T-3", "name": "x"})
        n = self.d.q1("SELECT COUNT(*) AS n FROM classifications")["n"]
        self.err(400, self.d.import_classifications, rec, "코드,이름,상위코드,보존연수\nI-1,정상,,3\nI-2,나쁨,,999\n")
        self.assertEqual(self.d.q1("SELECT COUNT(*) AS n FROM classifications")["n"], n)                  # all-or-nothing
        self.assertEqual(self.d.import_classifications(rec, "코드,이름,상위코드,보존연수\nI-1,정상,,3\nI-1-a,자식,I-1,5\n")["imported"], 2)

    def test_unknown_class_is_rejected_and_inactive_is_hidden(self):
        self.err(400, self.d.register, self.U["kim"], "c.md", b"# x\n\nx", folder_id=self.fid, class_code="NOPE")
        self.d.save_classification(self.U["rec"], {"code": "OFF-1", "name": "중단", "retention_years": 1, "active": False})
        self.assertNotIn("OFF-1", [c["code"] for c in self.d.classifications(self.U["kim"])])
        self.assertIn("OFF-1", [c["code"] for c in self.d.classifications(self.U["rec"], include_inactive=True)])

    def test_retention_is_the_longer_of_type_and_class_and_permanent_blocks_deletion(self):
        self.d.save_classification(self.U["rec"], {"code": "R-5", "name": "5년", "retention_years": 5})
        self.d.save_classification(self.U["rec"], {"code": "R-P", "name": "영구", "retention_years": 99})
        a = self.d.register(self.U["kim"], "a.md", b"# a\n\nretention a", folder_id=self.fid, class_code="R-5")
        row = self.d.row(a["id"])
        self.assertTrue("2031-01-01" <= row["retention_until"] <= "2031-12-31" or row["retention_until"] > "2030-01-01")
        self.err(409, self.d.delete, self.U["kim"], a["id"])                                                # retention applies to everyone
        p = self.d.register(self.U["kim"], "p.md", b"# p\n\npermanent", folder_id=self.fid, class_code="R-P")
        self.assertEqual(self.d.row(p["id"])["retention_until"], "9999-12-31")
        self.err(409, self.d.delete, self.U["kim"], p["id"])
        self.assertEqual(self.d.get(self.U["kim"], p["id"])["class_name"], "영구")

    def test_class_can_be_changed_later_via_metadata(self):
        a = self.d.register(self.U["kim"], "m.md", b"# m\n\nmeta class", folder_id=self.fid)
        out = self.d.update_meta(self.U["kim"], a["id"], class_code="02-01")
        self.assertEqual(out["class_name"], "대관 계약")
        self.assertIsNone(self.d.update_meta(self.U["kim"], a["id"], class_code="")["class_name"])


class Disposal(Base):
    def old_doc(self, owner="kim", **kw):
        doc = self.d.register(self.U[owner], "old.md", f"# old {kw.pop('tag', 'x')}\n\nold document".encode(), folder_id=self.fid, **kw)
        self.d.x("UPDATE dms_docs SET retention_until=? WHERE id=?", ("2020-01-01", doc["id"]))
        return doc

    def test_candidates_exclude_unexpired_held_and_in_review(self):
        ok = self.old_doc(tag="a")
        held = self.old_doc(tag="b")
        self.d.set_hold(self.U["admin"], held["id"], True, "소송")
        fresh = self.d.register(self.U["kim"], "f.md", b"# f\n\nfresh", folder_id=self.fid)
        ids = {c["doc_id"] for c in self.d.disposal_candidates(self.U["rec"])}
        self.assertIn(ok["id"], ids)
        self.assertNotIn(held["id"], ids)
        self.assertNotIn(fresh["id"], ids)
        self.err(403, self.d.disposal_candidates, self.U["kim"])
        c = next(c for c in self.d.disposal_candidates(self.U["rec"]) if c["doc_id"] == ok["id"])
        self.assertNotIn("old document", json.dumps(c))                                                   # metadata only, never content

    def test_full_procedure_with_separation_of_duties_and_ledger(self):
        doc = self.old_doc(tag="proc")
        req = self.d.request_disposal(self.U["rec"], [doc["id"]], "보존기간 경과")
        self.err(409, self.d.request_disposal, self.U["rec"], [doc["id"]], "again")                        # already in a pending request
        self.err(400, self.d.request_disposal, self.U["rec"], [doc["id"]], "")
        self.err(403, self.d.decide_disposal, self.U["rec"], req["id"], True)                              # records staff cannot approve
        res = self.d.decide_disposal(self.U["admin"], req["id"], True, "승인")
        self.assertEqual((res["disposed"], res["skipped"]), (1, 0))
        self.assertIsNone(self.d.row(doc["id"]))                                                          # really gone
        self.assertFalse((self.d.repo / str(doc["id"])).exists())
        led = next(r for r in self.d.disposal_ledger(self.U["rec"]) if r["doc_no"] == doc["doc_no"])
        self.assertEqual((led["requested_by"], led["approved_by"], len(led["sha256"])), ("rec", "admin", 64))
        self.assertIn(doc["doc_no"], self.d.disposal_ledger_csv(self.U["admin"]))
        self.assertTrue(self.d.store.verify_audit()[0])
        self.err(404, self.d.decide_disposal, self.U["admin"], req["id"], True)                            # decided once

    def test_admin_cannot_approve_their_own_request(self):
        doc = self.old_doc(tag="self")
        req = self.d.request_disposal(self.U["admin"], [doc["id"]], "내가 신청")
        self.err(403, self.d.decide_disposal, self.U["admin"], req["id"], True)
        self.app.cfg["disposal"] = {"allow_self_approval": True}
        try:
            self.assertEqual(self.d.decide_disposal(self.U["admin"], req["id"], True)["disposed"], 1)
        finally:
            self.app.cfg["disposal"] = {}

    def test_items_changed_or_held_after_the_request_are_skipped(self):
        a, b, c = self.old_doc(tag="s1"), self.old_doc(tag="s2"), self.old_doc(tag="s3")
        req = self.d.request_disposal(self.U["rec"], [a["id"], b["id"], c["id"]], "일괄")
        self.d.set_hold(self.U["admin"], a["id"], True, "추가 소송")
        self.d.update_meta(self.U["kim"], b["id"], description="신청 후 수정")
        res = self.d.decide_disposal(self.U["admin"], req["id"], True)
        self.assertEqual((res["disposed"], res["skipped"]), (1, 2))
        self.assertIsNotNone(self.d.row(a["id"]))
        self.assertIsNotNone(self.d.row(b["id"]))
        self.assertIsNone(self.d.row(c["id"]))
        items = {i["doc_no"]: i["result"] for i in next(x for x in self.d.list_disposals(self.U["rec"]) if x["id"] == req["id"])["items"]}
        self.assertTrue(items[a["doc_no"]].startswith("제외"))

    def test_reject_keeps_documents_and_notifies_the_requester(self):
        doc = self.old_doc(tag="rej")
        req = self.d.request_disposal(self.U["rec"], [doc["id"]], "x")
        self.d.decide_disposal(self.U["admin"], req["id"], False, "아직 필요")
        self.assertIsNotNone(self.d.row(doc["id"]))
        self.assertTrue([n for n in self.d.notifications(self.U["rec"])["items"] if n["kind"] == "disposal_result" and "반려" in n["text"]])
        self.assertIn(doc["id"], {c["doc_id"] for c in self.d.disposal_candidates(self.U["rec"])})        # candidate again


if __name__ == "__main__":
    unittest.main()
