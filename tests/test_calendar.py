import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.errors import DmsError  # noqa: E402


class CalendarTest(test_platform.DemoCase):
    def cal(self, uid, month):
        return self.app.dms.calendar(self.app.users[uid], month)["events"]

    def kinds(self, ev):
        return {(e["kind"], e["doc_no"]) for e in ev}

    def test_contract_due_from_db_and_document_for_authorised_users(self):
        ev = self.cal("kim", "2026-12")
        k = self.kinds(ev)
        self.assertIn(("db_due", "CT-2024-001"), k)                                   # business DB row
        self.assertTrue([e for e in ev if e["kind"] == "due" and "MICE 위크" in e["title"]])   # date written in the contract summary
        self.assertTrue(all(e["date"].startswith("2026-12") for e in ev))

    def test_provincial_contract_visible_only_to_its_department(self):
        self.assertIn(("db_due", "CT-2031-003"), self.kinds(self.cal("jung", "2026-10")))
        self.assertNotIn(("db_due", "CT-2031-003"), self.kinds(self.cal("kim", "2026-10")))      # row policy: owner_dept
        self.assertFalse([e for e in self.cal("kim", "2026-10") if "도청" in e["title"] and e["kind"] == "due"])

    def test_outsider_sees_no_contract_dates(self):
        for month in ("2026-10", "2026-12", "2027-03"):
            self.assertFalse([e for e in self.cal("choi", month) if e["kind"] in ("db_due", "due")], month)

    def test_expiry_and_retention_events_respect_read_permission(self):
        # 복지안내_2024 expired on 2025-01-01: its owner (admin) still sees it, ordinary readers do not
        self.assertTrue([e for e in self.cal("admin", "2025-01") if e["kind"] == "expiry" and "복지" in e["title"]])
        self.assertFalse([e for e in self.cal("choi", "2025-01") if "복지" in e["title"]])
        # a hidden document never appears: park's finance-only document date must not leak to choi
        d = self.app.dms
        r = d.register(self.app.users["park"], "h.md", "# 비공개\n\n재무 전용 비공개캘린더.".encode(), folder_id=d.q1("SELECT id FROM folders WHERE name='경영지원팀'")["id"], expires_at="2030-05-05")
        self.assertTrue([e for e in self.cal("park", "2030-05") if e["doc_id"] == r["id"]])
        self.assertFalse([e for e in self.cal("choi", "2030-05") if e["doc_id"] == r["id"]])

    def test_pending_approval_for_me_is_listed(self):
        ev = self.cal("park", __import__("datetime").date.today().strftime("%Y-%m"))
        self.assertTrue([e for e in ev if e["kind"] == "task"])

    def test_bad_month(self):
        for bad in ("2026-13", "abc", "2026-1"):
            with self.assertRaises(DmsError):
                self.app.dms.calendar(self.app.users["kim"], bad)

    def test_gateway_rows_refuses_masked_columns(self):
        gw = self.app.gateway
        self.assertTrue(gw.rows(self.app.users["park"], "contracts", ["id", "margin_rate"]))
        self.assertEqual(gw.rows(self.app.users["kim"], "contracts", ["id", "margin_rate"]), [])      # margin_rate: finance only
        self.assertEqual(gw.rows(self.app.users["kim"], "nope", ["id"]), [])


if __name__ == "__main__":
    unittest.main()
