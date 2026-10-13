import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from edm_ai.config import App  # noqa: E402
from edm_ai.llm import citations_valid  # noqa: E402
from edm_ai.models import Acl, Principal  # noqa: E402
from edm_ai.text import chunk_text, tokenize  # noqa: E402

DEMO = ROOT / "examples" / "demo"


class DemoCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build an isolated copy of the demo workspace in a temp dir.
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        import shutil
        shutil.copytree(DEMO / "docs", t / "docs")
        for f in ("users.json",):
            shutil.copy(DEMO / f, t / f)
        cfg = json.loads((DEMO / "config.json").read_text("utf-8"))
        cfg["store"], cfg["structured"]["path"] = "store.db", "erp.db"
        (t / "config.json").write_text(json.dumps(cfg), "utf-8")
        src = (DEMO / "build_demo.py").read_text("utf-8")
        db = sqlite3.connect(t / "erp.db")
        db.executescript(src.split('db.executescript("""')[1].split('""")')[0])
        db.commit()
        db.close()
        cls.app = App(str(t / "config.json"))
        cls.app.ingest()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, uid, q):
        return self.app.engine.ask(self.app.users[uid], q)

    def refs(self, ans):
        return {e.ref.split("#")[0] for e in ans.evidence}

    def test_doc_acl_trimming(self):
        q = "PRJ-2024 한빛전자 프로젝트 성과 보너스 원가 마진"
        sales, fin, hr = self.ask("kim", q), self.ask("park", q), self.ask("lee", q)
        self.assertNotIn("hr/2024_성과평가_및_보상.md", self.refs(sales))
        self.assertNotIn("finance/PRJ-2024_원가분석.md", self.refs(sales))
        self.assertIn("finance/PRJ-2024_원가분석.md", self.refs(fin))
        self.assertIn("hr/2024_성과평가_및_보상.md", self.refs(hr))
        self.assertNotIn("보너스", sales.text)

    def test_external_partner_sees_only_open_docs(self):
        ans = self.ask("choi", "대성정밀 PRJ-2025 도면 공차 검토")
        self.assertIn("drawings/대성정밀_도면검토_PRJ-2025.md", self.refs(ans))
        self.assertFalse(self.refs(self.ask("choi", "한빛전자 계약 지체상금")) & {"contracts/한빛전자_MES_계약요약.md"})

    def test_column_masking_and_rows(self):
        sales = self.ask("kim", "한빛전자 계약 금액")
        row = next(e for e in sales.evidence if e.ref == "contracts:CT-2024-001")
        self.assertIn("480000000", row.text)
        self.assertIn("margin_rate", row.masked)
        self.assertNotIn("0.18", row.text)
        fin = self.ask("park", "한빛전자 계약 금액")
        self.assertIn("0.18", next(e for e in fin.evidence if e.ref == "contracts:CT-2024-001").text)

    def test_row_level_policy(self):
        # PRJ-2031 belongs to consulting: sales must not see the row, consulting must.
        self.assertNotIn("contracts:CT-2031-003", {e.ref for e in self.ask("kim", "PRJ-2031 누리건설 계약").evidence})
        self.assertIn("contracts:CT-2031-003", {e.ref for e in self.ask("jung", "PRJ-2031 누리건설 계약").evidence})
        # invoices are finance-only
        self.assertFalse([e for e in self.ask("kim", "PRJ-2024 청구 미수").evidence if e.ref.startswith("invoices")])
        self.assertTrue([e for e in self.ask("park", "PRJ-2024 청구 미수").evidence if e.ref.startswith("invoices")])

    def test_doc_to_db_hop(self):
        # The question names neither project nor contract; the entity comes from the best document.
        ans = self.ask("park", "지체상금 조항이 있는 계약")
        self.assertTrue([e for e in ans.evidence if e.kind == "row"])

    def test_no_duplicate_rows(self):
        refs = [e.ref for e in self.ask("park", "한빛전자 PRJ-2024 CT-2024-001").evidence if e.kind == "row"]
        self.assertEqual(len(refs), len(set(refs)))

    def test_refusal_without_evidence(self):
        ans = self.ask("kim", "오늘 점심 메뉴 추천해줘")
        self.assertTrue(ans.refused)
        self.assertEqual(ans.evidence, [])

    def test_every_extractive_line_is_cited(self):
        ans = self.ask("park", "한빛전자 납기 지연")
        body = [ln for ln in ans.text.splitlines() if ln.startswith("- ")]
        self.assertTrue(body)
        self.assertTrue(all("[E" in ln for ln in body))

    def test_sql_injection_value_is_inert(self):
        gw = self.app.gateway
        rows = gw.lookup(self.app.users["park"], {"project:PRJ-2024' OR '1'='1", "customer:x\"; DROP TABLE contracts;--"})
        self.assertEqual(rows, [])
        self.assertTrue(gw.lookup(self.app.users["park"], {"project:PRJ-2024"}))

    def test_gateway_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.app.gateway.db.execute("DELETE FROM contracts")

    def test_audit_written_without_content(self):
        self.ask("kim", "한빛전자 계약")
        log = self.app.store.audit_log(1)[0]
        self.assertEqual(log["user"], "kim")
        self.assertTrue(log["evidence"])

    def test_acl_change_propagates_on_reingest(self):
        t = Path(self.tmp.name)
        mf = t / "docs" / "manifest.json"
        orig = mf.read_text("utf-8")
        try:
            m = json.loads(orig)
            m["rules"] = [r for r in m["rules"] if r["glob"] != "drawings/*"] + \
                         [{"glob": "drawings/*", "acl": {"roles": ["hr"], "level": 3}}]
            mf.write_text(json.dumps(m), "utf-8")
            stats = self.app.ingest()
            self.assertEqual(stats["added"], 1)  # only the re-ACL'd doc is reprocessed
            ans = self.ask("choi", "대성정밀 PRJ-2025 도면 공차 검토")
            self.assertFalse([e for e in ans.evidence if e.kind == "doc"])
        finally:
            mf.write_text(orig, "utf-8")
            self.app.ingest()


class UnitCase(unittest.TestCase):
    def test_acl_rules(self):
        p = Principal("u", roles=frozenset({"x"}), dept="d", level=2)
        self.assertTrue(Acl().allows(p))
        self.assertFalse(Acl(level=3).allows(p))
        self.assertFalse(Acl(roles=("y",)).allows(p))
        self.assertTrue(Acl(roles=("x",), level=2).allows(p))
        self.assertFalse(Acl(roles=("x",), deny_users=("u",)).allows(p))

    def test_tokenizer_matches_josa_variants(self):
        self.assertTrue({"한빛", "빛전", "전자"} <= set(tokenize("한빛전자의")))

    def test_heading_is_not_glued_to_body(self):
        self.assertEqual(chunk_text("## 제목\n본문입니다."), ["[제목] 본문입니다."])

    def test_citation_guard(self):
        self.assertTrue(citations_valid("금액은 4.8억 [E1]", 2))
        self.assertFalse(citations_valid("출처 없음", 2))
        self.assertFalse(citations_valid("환각 인용 [E9]", 2))

    def test_llm_fallback_on_bad_citations(self):
        from edm_ai.engine import Engine
        from edm_ai.entities import EntityExtractor
        from edm_ai.store import DocumentStore

        class Bad:
            def complete(self, q, e):
                return "근거 없는 답변"

        s = DocumentStore()
        s.upsert("a.md", "a", Acl(), [("한빛 계약 납기", frozenset())], "x")
        ans = Engine(s, None, EntityExtractor(), Bad()).ask(Principal("u"), "한빛 납기")
        self.assertEqual(ans.generator, "extractive")

        class Good:
            def complete(self, q, e):
                return "납기 관련 문서가 있습니다 [E1]"

        ans = Engine(s, None, EntityExtractor(), Good()).ask(Principal("u"), "한빛 납기")
        self.assertEqual(ans.generator, "llm")


if __name__ == "__main__":
    unittest.main()
