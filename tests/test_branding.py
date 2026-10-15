import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#f08a24"/></svg>'


class Branding(test_platform.DemoCase):
    def setUp(self):
        self.b = self.app.branding
        self.otmp = tempfile.TemporaryDirectory()
        self._dir = self.b.dir
        self.b.dir = Path(self.otmp.name) / "branding"

    def tearDown(self):
        self.b.dir = self._dir
        self.otmp.cleanup()

    def test_validation(self):
        self.assertFalse(self.b.info()["logo"])
        for name, data in (("a.exe", PNG), ("a.png", b"not a png"), ("a.png", b""), ("a.png", PNG + b"x" * 1_100_000), ("a.svg", b"<html></html>"),
                           ("a.svg", SVG.replace(b"<rect", b"<script>alert(1)</script><rect")), ("a.svg", SVG.replace(b"<rect", b'<rect onload="x()"')),
                           ("a.svg", SVG.replace(b"<rect", b'<image href="https://evil.example/x.png"/><rect')), ("a.webp", PNG)):
            with self.assertRaises(DmsError, msg=name):
                self.b.save(name, data)
        self.assertTrue(self.b.save("logo.png", PNG)["logo"])
        self.assertTrue(self.b.save("l.svg", SVG)["logo"])
        self.assertEqual(self.b.get()[1], "image/svg+xml")
        self.assertEqual(len(list(self.b.dir.iterdir())), 1)                       # replaced, not accumulated
        self.assertFalse(self.b.delete()["logo"])
        self.assertTrue(self.b.save("hero.png", PNG + b"x" * 1_500_000, "hero")["hero"])                 # hero may be larger than a logo
        with self.assertRaises(DmsError):
            self.b.save("hero.png", PNG + b"x" * 3_200_000, "hero")
        with self.assertRaises(DmsError):
            self.b.save("x.png", PNG, "other")
        self.assertEqual(self.b.get("hero")[1], "image/png")
        self.assertIsNone(self.b.get("logo"))
        self.assertFalse(self.b.delete("hero")["hero"])

    def test_served_publicly_with_sandbox_and_admin_only_upload(self):
        from http.server import ThreadingHTTPServer
        from edm_ai.server import make_handler
        import base64
        import json
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, True))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(method, path, user=None, body=None):
            h = {"X-User-Id": user} if user else {}
            data = json.dumps(body).encode() if body is not None else None
            if data:
                h["Content-Type"] = "application/json"
            try:
                with urllib.request.urlopen(urllib.request.Request(base + path, data, h, method=method), timeout=10) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers, e.read()
        try:
            put = {"filename": "l.svg", "content_b64": base64.b64encode(SVG).decode()}
            self.assertEqual(call("PUT", "/api/dms/admin/branding/logo", "kim", put)[0], 403)
            self.assertEqual(call("PUT", "/api/dms/admin/branding/logo", "admin", put)[0], 200)
            code, hdr, body = call("GET", "/brand-logo")
            self.assertEqual((code, body), (200, SVG))
            self.assertIn("sandbox", hdr["Content-Security-Policy"])
            self.assertEqual(hdr["X-Content-Type-Options"], "nosniff")
            self.assertTrue(json.loads(call("GET", "/api/config")[2])["logo"])
            self.assertEqual(call("DELETE", "/api/dms/admin/branding/logo", "admin")[0], 200)
            self.assertEqual(call("GET", "/brand-logo")[0], 404)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
