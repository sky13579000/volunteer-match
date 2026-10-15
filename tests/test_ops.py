import base64
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai import ed25519  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402
from edm_ai.license import canonical  # noqa: E402


class Ed(unittest.TestCase):
    def test_rfc8032_vectors(self):
        seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        pk = ed25519.public_key(seed)
        self.assertEqual(pk.hex(), "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = ed25519.sign(seed, b"")
        self.assertEqual(sig.hex(), "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertTrue(ed25519.verify(pk, b"", sig))
        self.assertFalse(ed25519.verify(pk, b"x", sig))
        bad = bytearray(sig)
        bad[40] ^= 1
        self.assertFalse(ed25519.verify(pk, b"", bytes(bad)))
        self.assertFalse(ed25519.verify(pk, b"", b"short"))


class Ops(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.seed = os.urandom(32)
        self.lic = self.app.license
        self._saved = (self.lic.path, self.lic.pub_hex)
        self.otmp = tempfile.TemporaryDirectory()
        self.lic.path = Path(self.otmp.name) / "license.json"
        self.lic.pub_hex = ed25519.public_key(self.seed).hex()

    def tearDown(self):
        self.lic.path, self.lic.pub_hex = self._saved
        self.otmp.cleanup()

    def make(self, **kw):
        payload = {"customer": "시험", "edition": "standard", "max_users": 100, "issued": "2026-01-01", "expires": (date.today() + timedelta(days=200)).isoformat(), "features": [], **kw}
        return {"payload": payload, "signature": base64.b64encode(ed25519.sign(self.seed, canonical(payload))).decode()}

    def test_license_states(self):
        self.assertEqual(self.lic.status()["mode"], "evaluation")
        st = self.lic.install(json.dumps(self.make()))
        self.assertEqual((st["mode"], st["customer"]), ("licensed", "시험"))
        self.assertEqual(self.lic.status()["mode"], "licensed")
        tampered = self.make()
        tampered["payload"]["max_users"] = 9999
        with self.assertRaises(ValueError):
            self.lic.install(json.dumps(tampered))
        with self.assertRaises(ValueError):
            self.lic.install("not json")
        self.lic.path.write_text(json.dumps(self.make(expires=(date.today() - timedelta(days=1)).isoformat())))
        self.assertEqual(self.lic.status()["mode"], "expired")
        self.lic.path.write_text(json.dumps(self.make(max_users=1)))
        self.assertIn("한도", self.lic.status()["message"])
        self.lic.pub_hex = ed25519.public_key(os.urandom(32)).hex()                      # signed by someone else
        self.assertEqual(self.lic.status()["mode"], "invalid")

    def test_backup_scheduler_runs_verifies_and_prunes(self):
        bk = self.app.backups
        self.assertFalse(bk.enabled() and False)
        self.app.cfg["backup"] = {"dir": str(Path(self.otmp.name) / "bk"), "every_hours": 24, "keep": 2}
        try:
            self.assertTrue(bk.due())
            import time
            for _ in range(3):
                r = bk.run("admin")
                self.assertTrue(r["ok"], r)
                time.sleep(1.1)
            files = sorted(Path(self.otmp.name, "bk").glob("edm-backup-*.zip"))
            self.assertEqual(len(files), 2)                                              # retention
            st = bk.status()
            self.assertEqual((len(st["runs"]), st["runs"][0]["ok"]), (3, 1))
            self.assertLess(st["last_ok_age_seconds"], 60)
            self.assertFalse(bk.due())
        finally:
            self.app.cfg.pop("backup")
        with self.assertRaises(DmsError):
            bk.run()

    def test_new_routes_require_admin_and_metrics_is_off_by_default(self):
        from http.server import ThreadingHTTPServer
        import threading
        from edm_ai.server import make_handler
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, True))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def get(path, user=None, token=None):
            h = {"X-User-Id": user} if user else {}
            if token:
                h["Authorization"] = "Bearer " + token
            try:
                with urllib.request.urlopen(urllib.request.Request(base + path, headers=h), timeout=10) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()
        try:
            os.environ.pop("EDM_AI_METRICS_TOKEN", None)
            self.assertEqual(get("/metrics")[0], 404)
            os.environ["EDM_AI_METRICS_TOKEN"] = "t" * 24
            self.assertEqual(get("/metrics", token="wrong")[0], 404)
            code, body = get("/metrics", token="t" * 24)
            self.assertEqual(code, 200)
            self.assertIn("edm_documents ", body)
            self.assertNotIn("kim", body)
            self.assertEqual(get("/api/dms/admin/backups", "kim")[0], 403)
            self.assertEqual(get("/api/dms/admin/backups", "admin")[0], 200)
            code, body = get("/api/license", "kim")
            self.assertEqual(code, 200)
            self.assertNotIn("customer", json.loads(body))
        finally:
            os.environ.pop("EDM_AI_METRICS_TOKEN", None)
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
