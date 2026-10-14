"""Dialect unit tests (no DB needed) + PostgreSQL integration (runs only if EDM_TEST_PG_DSN points at a
database seeded with deploy/postgres/seed.sql, connecting as the SELECT-only edm_ro role)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edm_ai.dbdrivers import _NAMED, Oracle, Postgres  # noqa: E402

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"
PG = os.environ.get("EDM_TEST_PG_DSN")


class Dialects(unittest.TestCase):
    def test_placeholder_rewrite_ignores_casts(self):
        self.assertEqual(_NAMED.sub(lambda m: f"%({m.group(1)})s", "a = :x AND b::int = :y_2"),
                         "a = %(x)s AND b::int = %(y_2)s")

    def test_oracle_limit_syntax(self):
        self.assertEqual(Oracle.limit(object.__new__(Oracle), 5), "FETCH FIRST 5 ROWS ONLY")

    def test_limit_is_forced_to_int(self):
        self.assertEqual(Postgres.limit(object.__new__(Postgres), "7"), "LIMIT 7")
        with self.assertRaises(ValueError):
            Postgres.limit(object.__new__(Postgres), "1; DROP TABLE x")


@unittest.skipUnless(PG, "set EDM_TEST_PG_DSN to run PostgreSQL integration tests")
class PostgresIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from edm_ai.config import App
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cfg = json.loads((DEMO / "config.postgres.json").read_text("utf-8"))
        cfg.update(store="s.db", users=str(DEMO / "users.json"))
        cfg["docs"] = {"root": str(DEMO / "docs"), "manifest": str(DEMO / "docs" / "manifest.json")}
        os.environ["ERP_DSN"] = PG
        (t / "c.json").write_text(json.dumps(cfg), "utf-8")
        cls.app = App(str(t / "c.json"))
        cls.app.ingest()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_golden_set_matches_sqlite_behaviour(self):
        from edm_ai.evaluate import run_eval
        r = run_eval(self.app, DEMO / "golden.json")
        self.assertEqual((r["leaks"], r["failures"]), (0, []))

    def test_session_is_read_only(self):
        with self.assertRaises(Exception) as cm:
            self.app.gateway.db.query("DELETE FROM contracts")
        # rejected by whichever layer fires first: server read-only session or the SELECT-only GRANT
        self.assertRegex(str(cm.exception).lower(), "read-only|permission denied")

    @unittest.skipUnless(os.environ.get("EDM_TEST_PG_ADMIN_DSN"), "needs a DSN with write privileges")
    def test_session_read_only_even_if_account_can_write(self):
        pg = Postgres(os.environ["EDM_TEST_PG_ADMIN_DSN"])  # layer 2: server-side read-only session
        with self.assertRaises(Exception) as cm:
            pg.query("DELETE FROM contracts")
        self.assertIn("read-only", str(cm.exception).lower())
        self.assertEqual(len(pg.query("SELECT 1 AS x")), 1)

    def test_decimal_aggregates_and_grouping(self):
        e = next(x for x in self.app.engine.ask(self.app.users["park"], "고객사별 계약금액 합계").evidence
                 if x.ref == "metric:contract_by_customer")
        self.assertIn("제주관광공사: 480,000,000", e.text)

    def test_percent_in_values_is_safe(self):
        self.assertEqual(self.app.gateway.lookup(self.app.users["park"], {"customer:100%", "project:%"}), [])

    def test_row_policy_and_masking(self):
        ids = {e.ref for e in self.app.engine.ask(self.app.users["kim"], "EVT-2031 제주도청 계약").evidence}
        self.assertNotIn("contracts:CT-2031-003", ids)


if __name__ == "__main__":
    unittest.main()


ORA = os.environ.get("ERP_ORA_DSN") and os.environ.get("ERP_ORA_USER")


@unittest.skipUnless(ORA, "set ERP_ORA_DSN/ERP_ORA_USER/ERP_ORA_PASSWORD (seed with deploy/oracle/seed.sql) to run Oracle tests")
class OracleIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from edm_ai.config import App
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = json.loads((DEMO / "config.oracle.json").read_text("utf-8"))
        cfg.update(store="s.db", users=str(DEMO / "users.json"))
        cfg["docs"] = {"root": str(DEMO / "docs"), "manifest": str(DEMO / "docs" / "manifest.json")}
        (Path(cls.tmp.name) / "c.json").write_text(json.dumps(cfg), "utf-8")
        cls.app = App(str(Path(cls.tmp.name) / "c.json"))
        cls.app.ingest()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_golden_set_identical_to_other_databases(self):
        from edm_ai.evaluate import run_eval
        r = run_eval(self.app, DEMO / "golden.json")
        self.assertEqual((r["leaks"], r["failures"]), (0, []))

    def test_session_is_read_only_even_for_the_schema_owner(self):
        with self.assertRaises(Exception) as cm:
            self.app.gateway.db.query("DELETE FROM CONTRACTS")
        self.assertRegex(str(cm.exception), "ORA-01456|read-only|only SELECT")
        self.assertEqual(self.app.gateway.db.query('SELECT COUNT(*) AS "n" FROM CONTRACTS')[0]["n"], 3)

    def test_reserved_word_bind_and_unused_params(self):
        # row policies bind :dept; :level is an Oracle reserved word and :uid is unused in the SQL
        rows = self.app.gateway.lookup(self.app.users["kim"], {"project:EVT-2024"})
        self.assertEqual([r["key"] for r in rows], ["CT-2024-001"])
        self.assertIn("margin_rate".upper(), rows[0]["masked"])

    def test_aggregate_with_group_by(self):
        e = next(x for x in self.app.engine.ask(self.app.users["park"], "고객사별 계약금액 합계").evidence
                 if x.ref == "metric:contract_by_customer")
        self.assertIn("제주관광공사: 480,000,000", e.text)

    def test_row_policy_blocks_other_departments(self):
        self.assertEqual(self.app.gateway.lookup(self.app.users["kim"], {"project:EVT-2031"}), [])
