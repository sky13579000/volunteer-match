import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_records  # noqa: E402
from edm_ai.dms_privacy import wipe_tree  # noqa: E402

PII = "# 명단\n\n홍길동 900101-1234567 연락처 010-1234-5678 메일 hong@example.com".encode()


class Registry(test_records.Base):
    def test_register_crud_deadlines_and_notification(self):
        rec, kim = self.U["rec"], self.U["kim"]
        self.err(403, self.d.pii_registry, kim)
        self.err(400, self.d.pii_registry_save, rec, {"name": "", "purpose": ""})
        self.err(400, self.d.pii_registry_save, rec, {"name": "a", "purpose": "b", "destroy_by": "내일"})
        r = self.d.pii_registry_save(rec, {"name": "행사 참가자 명단", "purpose": "행사 운영", "items": "성명, 연락처", "destroy_by": "2020-01-01", "manager": "rec"})
        s = self.d.pii_registry_save(rec, {"name": "장기 보관", "purpose": "법정 보관", "destroy_by": "2999-01-01"})
        reg = self.d.pii_registry(rec)
        self.assertEqual(reg["overdue"], 1)
        self.assertEqual(next(i for i in reg["items"] if i["id"] == r["id"])["due"], "overdue")
        self.assertIsNone(next(i for i in reg["items"] if i["id"] == s["id"])["due"])
        self.assertEqual(self.d.privacy_maintenance(), 1)
        self.assertEqual(self.d.privacy_maintenance(), 0)                                                   # once per day
        self.assertIn("privacy_due", [n["kind"] for n in self.d.notifications(rec)["items"]])
        self.assertIn("행사 참가자 명단", self.d.pii_registry_csv(rec))
        self.d.pii_registry_delete(rec, r["id"])
        self.assertEqual(self.d.pii_registry(rec)["overdue"], 0)


class Unmask(test_records.Base):
    def test_reason_required_logged_and_owner_told(self):
        doc = self.d.register(self.U["kim"], "p.md", PII, folder_id=self.fid, entries=[{"type": "user", "id": "park", "role": "viewer"}])
        self.assertNotIn("900101-1234567", self.d.get(self.U["park"], doc["id"])["text"])                   # masked on screen
        self.err(400, self.d.unmask, self.U["park"], doc["id"], "")
        self.err(404, self.d.unmask, self.U["choi"], doc["id"], "사유가 충분히 깁니다")                       # no read permission
        r = self.d.unmask(self.U["park"], doc["id"], "민원 대응을 위한 확인")
        self.assertIn("900101-1234567", r["text"])
        log = self.d.unmask_log(self.U["rec"])
        self.assertEqual((log[0]["user_id"], log[0]["doc_id"]), ("park", doc["id"]))
        self.assertNotIn("900101", json.dumps(log))
        self.assertIn("privacy_alert", [n["kind"] for n in self.d.notifications(self.U["kim"])["items"]])
        self.err(403, self.d.unmask_log, self.U["park"])
        self.app.cfg["privacy"] = {"unmask_roles": ["hr"]}
        try:
            self.err(403, self.d.unmask, self.U["park"], doc["id"], "역할이 필요한 정책")
        finally:
            self.app.cfg.pop("privacy")


class Destruction(test_records.Disposal):
    def test_anonymize_keeps_document_but_removes_identifiers_and_old_files(self):
        d = self.d.register(self.U["kim"], "pii.md", PII, folder_id=self.fid)
        self.d.x("UPDATE dms_docs SET retention_until=? WHERE id=?", ("2020-01-01", d["id"]))
        v1 = self.d.repo / str(d["id"]) / "1"
        self.assertTrue(v1.exists())
        self.err(400, self.d.request_disposal, self.U["rec"], [d["id"]], "x", "shred")
        req = self.d.request_disposal(self.U["rec"], [d["id"]], "보존기간 경과 후 통계용 보관", "anonymize")
        res = self.d.decide_disposal(self.U["admin"], req["id"], True, "")
        self.assertEqual(res["disposed"], 1)
        row = self.d.row(d["id"])
        self.assertTrue(row and row["props"].get("anonymized"))
        text = self.d._ver(row, row["current_ver"])["text"]
        self.assertNotIn("900101-1234567", text)
        self.assertNotIn("1234-5678", text)
        self.assertIn("명단", text)
        self.assertEqual(self.d.q1("SELECT COUNT(*) AS n FROM dms_versions WHERE doc_id=?", (d["id"],))["n"], 1)
        self.err(409, self.d.request_disposal, self.U["rec"], [d["id"]], "again", "anonymize")                  # already anonymised
        self.assertTrue(self.d.request_disposal(self.U["rec"], [d["id"]], "이제 영구 삭제")["id"])               # but may still be deleted later
        cert = self.d.disposal_certificate(self.U["rec"], req["id"])
        self.assertEqual(cert["method"], "anonymize")
        self.assertEqual(cert["items"][0]["method"], "anonymize")
        self.assertEqual(self.d.disposal_certificate(self.U["rec"], req["id"])["digest"], cert["digest"])    # reproducible

    def test_certificate_only_for_approved_and_wipe_removes_files(self):
        doc = self.old_doc(tag="cert")
        req = self.d.request_disposal(self.U["rec"], [doc["id"]], "보존기간 경과")
        self.err(404, self.d.disposal_certificate, self.U["rec"], req["id"])                                # not decided yet
        self.err(403, self.d.disposal_certificate, self.U["kim"], req["id"])
        self.d.decide_disposal(self.U["admin"], req["id"], True, "")
        cert = self.d.disposal_certificate(self.U["admin"], req["id"])
        self.assertTrue(cert["no"].startswith("DEST-") and len(cert["digest"]) == 64 and cert["items"][0]["doc_no"] == doc["doc_no"])
        self.assertFalse((self.d.repo / str(doc["id"])).exists())

    def test_wipe_tree_overwrites_before_unlink(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "x"
            (root / "1").mkdir(parents=True)
            (root / "1" / "a.bin").write_bytes(b"SECRET" * 1000)
            self.assertEqual(wipe_tree(root), 1)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
