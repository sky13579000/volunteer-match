import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai import auth  # noqa: E402
from edm_ai.server import make_handler  # noqa: E402

SECRET = "s" * 32


def call(base, path, method="GET", headers=None, body=None):
    req = urllib.request.Request(base + path, json.dumps(body).encode() if body else None, headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        os.environ["EDM_AI_SECRET"] = SECRET

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def serve(self, auth_cfg, demo=False, limit=60):
        self.app.cfg["auth"], self.app.cfg["rate_limit_per_min"] = auth_cfg, limit
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, demo))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_static_ui_has_strict_csp(self):
        base = self.serve({"mode": "token"})
        for path in ("/", "/app.js", "/app.css"):
            code, h, body = call(base, path)
            self.assertEqual(code, 200)
            self.assertIn("default-src 'none'", h["Content-Security-Policy"])
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(b"innerHTML", call(base, "/app.js")[2])   # UI renders data via textContent only

    def test_token_mode_rejects_spoofed_header(self):
        base = self.serve({"mode": "token"})
        self.assertEqual(call(base, "/api/me", headers={"X-User-Id": "admin"})[0], 401)
        tok = auth.issue(SECRET, "kim")
        code, _, b = call(base, "/api/me", headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual((code, json.loads(b)["id"]), (200, "kim"))
        self.assertEqual(call(base, "/api/me", headers={"Authorization": "Bearer " + tok[:-3] + "xxx"})[0], 401)

    def test_demo_users_only_in_demo_header_mode(self):
        self.assertEqual(call(self.serve({"mode": "token"}, demo=True), "/api/demo-users")[0], 404)
        self.assertEqual(call(self.serve({"mode": "header"}, demo=False), "/api/demo-users")[0], 404)
        code, _, b = call(self.serve({"mode": "header"}, demo=True), "/api/demo-users")
        self.assertEqual(code, 200)
        self.assertNotIn(b"roles", b)       # no role/ACL details leak to the login page

    def test_ask_is_permission_scoped_over_http(self):
        base = self.serve({"mode": "header"})
        def refs(uid):
            _, _, b = call(base, "/api/ask", "POST", {"X-User-Id": uid}, {"question": "EVT-2024 원가 마진 성과 보너스"})
            return {e["ref"].split("#")[0] for e in json.loads(b)["evidence"]}
        self.assertIn("finance/EVT-2024_원가분석.md", refs("park"))
        self.assertNotIn("finance/EVT-2024_원가분석.md", refs("kim"))
        self.assertNotIn("hr/2024_성과평가_및_보상.md", refs("kim"))

    def test_audit_admin_only_and_rate_limit(self):
        base = self.serve({"mode": "header"}, limit=2)
        self.assertEqual(call(base, "/api/audit", headers={"X-User-Id": "kim"})[0], 403)
        self.assertTrue(json.loads(call(base, "/api/audit", headers={"X-User-Id": "admin"})[2])["chain_ok"])
        codes = [call(base, "/api/ask", "POST", {"X-User-Id": "jung"}, {"question": "제주관광공사"})[0] for _ in range(4)]
        self.assertEqual(codes, [200, 200, 429, 429])

    def test_internal_error_returns_json_500_not_dropped_connection(self):
        base = self.serve({"mode": "header"})
        orig = self.app.engine.ask
        self.app.engine.ask = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom secret-path"))
        try:
            code, _, body = call(base, "/api/ask", "POST", {"X-User-Id": "kim"}, {"question": "제주관광공사"})
        finally:
            self.app.engine.ask = orig
        self.assertEqual(code, 500)
        self.assertNotIn(b"secret-path", body)

    def test_bad_requests(self):
        base = self.serve({"mode": "header"})
        h = {"X-User-Id": "kim"}
        self.assertEqual(call(base, "/api/ask", "POST", h, {"nope": 1})[0], 400)
        self.assertEqual(call(base, "/api/ask", "POST", h, {"question": "   "})[0], 400)
        self.assertEqual(call(base, "/api/nothing", headers=h)[0], 404)


if __name__ == "__main__":
    unittest.main()
