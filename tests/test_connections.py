import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeservers  # noqa: E402
import test_platform  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


class Conn(test_platform.DemoCase):
    def setUp(self):
        self.c = self.app.connections
        self.otmp = tempfile.TemporaryDirectory()
        self._path = self.c.path
        self.c.path = Path(self.otmp.name) / "connections.json"
        self.nas = Path(self.otmp.name) / "nas"
        (self.nas / "계약").mkdir(parents=True)
        (self.nas / "계약" / "a.md").write_text("# NAS 문서\n\nnasconn토큰", "utf-8")

    def tearDown(self):
        self.c.apply()
        self.c.path = self._path
        self.c.apply()
        self.otmp.cleanup()

    def test_path_source_test_save_sync_delete(self):
        bad = self.c.test_source({"type": "path", "path": str(self.nas / "없음")})
        self.assertFalse(bad["ok"])
        ok = self.c.test_source({"type": "path", "path": str(self.nas)})
        self.assertTrue(ok["ok"])
        self.assertIn("파일 1", ok["message"])
        with self.assertRaises(DmsError):
            self.c.save_source({"name": "x/y", "type": "path", "path": str(self.nas)})
        self.c.save_source({"name": "UI-NAS", "type": "path", "path": str(self.nas), "mode": "reference", "include": "*.md", "acl": {"roles": ["admin"], "level": 99}})
        self.assertIn("UI-NAS", self.app.sources.cfgs)
        listing = self.c.list()
        self.assertEqual(next(s for s in listing["sources"] if s["name"] == "UI-NAS")["origin"], "ui")
        r = self.app.sources.sync("UI-NAS")
        self.assertGreaterEqual(r.get("added", 0), 1)
        st = oct(self.c.path.stat().st_mode & 0o777)
        self.assertEqual(st, "0o600")
        self.c.delete_source("UI-NAS")
        self.assertNotIn("UI-NAS", self.app.sources.cfgs)
        with self.assertRaises(DmsError):
            self.c.delete_source("본부NAS")                                         # config.json entries are not deletable here

    def test_new_source_without_acl_is_admin_only(self):
        self.c.save_source({"name": "Safe", "type": "path", "path": str(self.nas)})
        self.assertEqual(self.app.sources.cfgs["Safe"]["acl"]["level"], 99)

    def test_mail_save_hides_password_and_keeps_it_on_edit(self):
        imap = fakeservers.ImapServer([], "u", "비밀번호!")
        try:
            spec = {"name": "UI메일", "host": "127.0.0.1", "port": imap.port, "ssl": False, "user": "u", "password": "비밀번호!", "mailbox": "INBOX"}
            t = self.c.test_mail(spec)
            self.assertTrue(t["ok"], t)
            self.c.save_mail(spec)
            self.assertNotIn("비밀번호", json.dumps(self.c.list(), ensure_ascii=False))
            self.assertTrue(next(m for m in self.c.list()["mail"] if m["name"] == "UI메일")["has_password"])
            self.c.save_mail({**spec, "password": "", "schedule_minutes": 15})
            self.assertEqual(self.app.mailbox.cfgs["UI메일"]["password"], "비밀번호!")        # blank = keep
            self.assertEqual(self.app.mailbox.cfgs["UI메일"]["schedule_minutes"], 15)
            self.assertTrue(self.c.test_mail({"name": "UI메일", "host": "127.0.0.1", "port": spec["port"], "ssl": False, "user": "u"})["ok"])   # stored password reused
            bad = self.c.test_mail({"host": "127.0.0.1", "port": 1, "ssl": False, "user": "u", "password": "x"})
            self.assertFalse(bad["ok"])
            with self.assertRaises(DmsError):
                self.c.save_mail({"name": "나쁜", "host": ""})
        finally:
            imap.close()

    def test_routes_need_admin(self):
        from http.server import ThreadingHTTPServer
        import urllib.request
        from edm_ai.server import make_handler
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, True))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for user, code in (("kim", 403), ("admin", 200)):
                req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/dms/admin/connections", headers={"X-User-Id": user})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        got = r.status
                except urllib.error.HTTPError as e:
                    got = e.code
                self.assertEqual(got, code)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
