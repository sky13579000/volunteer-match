import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai import mock_llm  # noqa: E402
from edm_ai.engine import Engine  # noqa: E402
from edm_ai.entities import EntityExtractor  # noqa: E402
from edm_ai.evaluate import run_eval  # noqa: E402
from edm_ai.llm import OpenAICompatClient, numbers_grounded  # noqa: E402
from edm_ai.llmcheck import run_check  # noqa: E402
from edm_ai.models import Acl, Principal  # noqa: E402
from edm_ai.store import DocumentStore  # noqa: E402
from edm_ai.vectors import OpenAICompatEmbedder  # noqa: E402

DEMO = test_platform.DEMO
EV = ["계약금액=480000000; 납기=2024-12-20; 계약번호=CT-2024-001", "계약금액은 4억 8천만원이다. 지체상금은 하루당 0.1%"]


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def fixed_chat(reply):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            raw = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass
    return H


class Grounding(unittest.TestCase):
    def test_numbers_grounded(self):
        ok = ["계약금액은 4억 8천만원입니다 [E1]", "계약금액은 480,000,000원 [E1]", "납기 2024-12-20, 계약번호 CT-2024-001",
              "지체상금 0.1% [E2]", "담당자는 12명입니다"]
        for a in ok:
            self.assertEqual(numbers_grounded(a, EV), [], a)
        self.assertEqual(numbers_grounded("계약금액은 5억원입니다 [E1]", EV), ["500,000,000원"])
        self.assertEqual(numbers_grounded("합계는 8억 5천만원", EV), ["850,000,000원"])
        self.assertTrue(numbers_grounded("청구 144,000,000원", EV))

    def test_engine_rejects_llm_that_invents_a_figure(self):
        s = DocumentStore()
        s.upsert("a.md", "계약", Acl(), [("한빛 계약금액은 4억 8천만원이다 납기 안내", frozenset())], "x")
        for reply, want in (("계약금액은 5억원입니다 [E1]", "extractive"), ("계약금액은 480,000,000원입니다 [E1]", "llm")):
            srv, url = serve(fixed_chat(reply))
            try:
                ans = Engine(s, None, EntityExtractor(), OpenAICompatClient(url, "m")).ask(Principal("u"), "한빛 계약금액")
            finally:
                srv.shutdown()
                srv.server_close()
            self.assertEqual(ans.generator, want, reply)


class LlmCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def levels(self, llm=None, emb=None):
        self.app.engine.llm, self.app.engine.embedder = llm, emb
        out, ok = run_check(self.app)
        return {m: l for l, m in out}, [l for l, _ in out], ok

    def test_no_configuration_fails(self):
        _, levels, ok = self.levels()
        self.assertFalse(ok)
        self.assertEqual(levels, ["FAIL"])

    def test_unreachable_servers_fail(self):
        _, levels, ok = self.levels(OpenAICompatClient("http://127.0.0.1:1/v1", "m", timeout=0.5),
                                    OpenAICompatEmbedder("http://127.0.0.1:1/v1", "e", timeout=0.5))
        self.assertFalse(ok)
        self.assertEqual(levels.count("FAIL"), 2)

    def test_model_without_citations_or_with_invented_numbers_fails(self):
        for reply in ("계약금액은 120,000,000원입니다", "계약금액은 999,000,000원입니다 [E1]"):
            srv, url = serve(fixed_chat(reply))
            try:
                _, levels, ok = self.levels(OpenAICompatClient(url, "m"))
            finally:
                srv.shutdown()
                srv.server_close()
            self.assertFalse(ok, reply)

    def test_good_answer_passes_and_mock_embedder_is_flagged_non_semantic(self):
        srv, url = serve(fixed_chat("계약금액은 1억 2천만원이고 납기는 2025-06-30입니다 [E1]"))
        msrv, murl = serve(mock_llm.H)
        try:
            by_msg, levels, ok = self.levels(OpenAICompatClient(url, "m"), OpenAICompatEmbedder(murl, "mock"))
        finally:
            for x in (srv, msrv):
                x.shutdown()
                x.server_close()
        self.assertTrue(ok)                                                    # nothing hard-failed
        self.assertIn("WARN", [l for m, l in by_msg.items() if "의미 유사도" in m])   # hashed vectors are not semantic: say so


class EvalQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.srv, cls.url = serve(mock_llm.H)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def test_extractive_passes_all_facts(self):
        self.app.engine.llm = self.app.engine.embedder = None
        r = run_eval(self.app, DEMO / "golden.json")
        self.assertEqual((r["failures"], r["ungrounded_numbers"], r["leaks"]), ([], [], 0))
        self.assertEqual(r["llm_share"], 0.0)

    def test_summarising_model_that_drops_key_facts_is_caught(self):
        self.app.engine.llm = OpenAICompatClient(self.url, "mock")
        try:
            r = run_eval(self.app, DEMO / "golden.json")
        finally:
            self.app.engine.llm = None
        self.assertGreater(r["llm_share"], 0.5)
        self.assertTrue([f for f in r["failures"] if f.startswith("MISSING FACT")])
        self.assertEqual(r["leaks"], 0)                                        # permissions unaffected by the model

    def test_must_not_contain_counts_as_leak(self):
        g = Path(self.tmp.name) / "g.json"
        g.write_text(json.dumps([{"user": "park", "question": "한빛전자 계약금액", "must_not_contain": ["한빛전자"]}]), "utf-8")
        self.app.engine.llm = None
        r = run_eval(self.app, g)
        self.assertEqual(r["leaks"], 1)


if __name__ == "__main__":
    unittest.main()
