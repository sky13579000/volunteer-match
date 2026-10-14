import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.client import EdmApiError, EdmClient  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402
from edm_ai.events import Events  # noqa: E402
from edm_ai.server import make_handler  # noqa: E402

HOOK = "hook-secret-0123456789abcdef"


class Receiver:
    def __init__(self, statuses=(200,)):
        self.got, self.statuses = [], list(statuses)
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.got.append(({k.lower(): v for k, v in self.headers.items()}, self.rfile.read(n)))
                code = outer.statuses.pop(0) if len(outer.statuses) > 1 else outer.statuses[0]
                self.send_response(code)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/hook"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def wait(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ApiKeys(test_platform.DemoCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ["EDM_AI_SECRET"] = "t" * 32
        cls.app.cfg["auth"], cls.app.cfg["rate_limit_per_min"] = {"mode": "token"}, 1000
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def make(self, user="kim", scope="read", days=30):
        return self.app.dms.create_api_key(self.app.users["admin"], "시험 키", user, scope, days)

    def test_only_admins_manage_keys_and_the_secret_is_stored_hashed(self):
        d, U = self.app.dms, self.app.users
        for fn in (lambda: d.create_api_key(U["kim"], "x", "kim"), lambda: d.list_api_keys(U["kim"]), lambda: d.revoke_api_key(U["kim"], 1)):
            with self.assertRaises(DmsError) as cm:
                fn()
            self.assertEqual(cm.exception.code, 403)
        k = self.make()
        self.assertTrue(k["key"].startswith("edmk_"))
        self.assertNotIn(k["key"], json.dumps(d.q("SELECT * FROM api_keys")))
        self.assertNotIn(k["key"], json.dumps(d.list_api_keys(U["admin"])))
        self.assertNotIn(k["key"], json.dumps(d.q("SELECT * FROM audit")))
        for bad in (("", "kim", "read", 30), ("n", "nobody", "read", 30), ("n", "kim", "root", 30), ("n", "kim", "read", "abc")):
            with self.assertRaises(DmsError):
                d.create_api_key(U["admin"], *bad)

    def test_key_acts_as_its_user_with_exactly_that_users_permissions(self):
        c = EdmClient(self.base, token=self.make("kim")["key"])
        self.assertTrue(c.ask("제주관광공사 계약 금액")["evidence"])
        self.assertTrue(c.search("지체상금")["items"])
        # kim may not read HR documents - neither may her key
        ans = c.ask("보너스 지급 대상 성과 등급")
        self.assertFalse([e for e in ans["evidence"] if e["ref"].startswith("hr/")])
        choi = EdmClient(self.base, token=self.make("choi")["key"])
        self.assertFalse([e for e in choi.ask("한빛 제주관광공사 지체상금 계약")["evidence"] if e["ref"].startswith("contracts/")])

    def test_read_scope_cannot_write_but_write_scope_can(self):
        ro = EdmClient(self.base, token=self.make("kim", "read")["key"])
        with self.assertRaises(EdmApiError) as cm:
            ro.comment(1, "x")
        self.assertEqual(cm.exception.status, 403)
        rw = EdmClient(self.base, token=self.make("kim", "write")["key"])
        f = Path(self.tmp.name) / "key_up.md"
        f.write_text("# 키 업로드\n\n키업로드본문유니크", "utf-8")
        d = rw.upload(str(f), doc_type="GEN")
        self.assertEqual(d["owner"], "kim")

    def test_keys_never_reach_the_admin_api_even_for_an_admin_user(self):
        key = self.make("admin", "write")["key"]
        c = EdmClient(self.base, token=key)
        for path in ("/api/dms/admin/api-keys", "/api/dms/admin/audit", "/api/audit"):
            req = urllib.request.Request(self.base + path, headers={"Authorization": "Bearer " + key})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 403, path)
        self.assertTrue(c.search("규정")["items"])                                        # ordinary calls still work

    def test_revoked_expired_and_unknown_keys_are_rejected(self):
        k = self.make()
        c = EdmClient(self.base, token=k["key"])
        self.assertTrue(c.search("규정")["items"])
        self.app.dms.revoke_api_key(self.app.users["admin"], k["id"])
        with self.assertRaises(EdmApiError) as cm:
            c.search("규정")
        self.assertEqual(cm.exception.status, 401)
        k2 = self.make()
        self.app.dms.x("UPDATE api_keys SET expires='2020-01-01T00:00:00+00:00' WHERE id=?", (k2["id"],))
        with self.assertRaises(EdmApiError):
            EdmClient(self.base, token=k2["key"]).search("규정")
        with self.assertRaises(EdmApiError):
            EdmClient(self.base, token="edmk_nope_nope").search("규정")
        statuses = {x["id"]: x["status"] for x in self.app.dms.list_api_keys(self.app.users["admin"])}
        self.assertEqual((statuses[k["id"]], statuses[k2["id"]]), ("revoked", "expired"))

    def test_last_used_is_recorded(self):
        k = self.make()
        EdmClient(self.base, token=k["key"]).search("규정")
        self.assertTrue(next(x for x in self.app.dms.list_api_keys(self.app.users["admin"]) if x["id"] == k["id"])["last_used"])


class Webhooks(unittest.TestCase):
    def setUp(self):
        os.environ["EDM_AI_TEST_HOOK"] = HOOK
        self.rx = Receiver()

    def tearDown(self):
        self.rx.close()

    def events(self, **kw):
        cfg = {"include_title": kw.pop("include_title", False), "webhooks": [{"name": "t", "url": self.rx.url, "secret_env": "EDM_AI_TEST_HOOK", "events": kw.pop("events", ["*"])}]}
        return Events(cfg, timeout=2, wait=(0, 0.05, 0.05))

    def test_signed_delivery_with_filtering_and_no_title_by_default(self):
        ev = self.events(events=["doc.approved"])
        row = {"id": 7, "doc_no": "CT-2026-0009", "title": "비밀 제목", "doc_type": "CT", "status": "approved", "owner": "kim", "level": 2}
        ev.emit("doc.created", row, "kim")                                              # filtered out
        ev.emit("doc.approved", row, "park", "v1")
        self.assertTrue(wait(lambda: self.rx.got))
        headers, body = self.rx.got[0]
        ts = headers["x-edm-timestamp"]
        self.assertEqual(headers["x-edm-signature"], Events.sign(HOOK, ts, body))
        self.assertEqual(headers["x-edm-event"], "doc.approved")
        data = json.loads(body)
        self.assertEqual((data["doc"]["doc_no"], data["actor"]), ("CT-2026-0009", "park"))
        self.assertNotIn("title", data["doc"])
        self.assertNotIn("비밀", body.decode())
        time.sleep(0.3)
        self.assertEqual(len(self.rx.got), 1)

    def test_title_only_when_enabled(self):
        ev = self.events(include_title=True)
        ev.emit("doc.created", {"id": 1, "doc_no": "A", "title": "제목공개", "doc_type": "GEN", "status": "approved", "owner": "kim", "level": 0}, "kim")
        self.assertTrue(wait(lambda: self.rx.got))
        self.assertIn("제목공개", self.rx.got[0][1].decode())

    def test_retries_on_server_errors_then_succeeds(self):
        self.rx.statuses = [500, 503, 200]
        ev = self.events()
        ev.emit("ping", None, "admin")
        self.assertTrue(wait(lambda: ev.sent == 1))
        self.assertEqual(len(self.rx.got), 3)
        self.assertEqual(ev.status()["recent"][0]["attempts"], 3)

    def test_client_errors_are_not_retried_and_failures_are_recorded(self):
        self.rx.statuses = [400]
        ev = self.events()
        ev.emit("ping", None, "admin")
        self.assertTrue(wait(lambda: ev.failed == 1))
        self.assertEqual(len(self.rx.got), 1)
        self.assertEqual(ev.status()["recent"][0]["status"], "failed")

    def test_dead_receiver_never_blocks_the_caller(self):
        self.rx.close()
        ev = self.events()
        t = time.time()
        for _ in range(50):
            ev.emit("ping", None, "admin")
        self.assertLess(time.time() - t, 0.5)
        self.rx = Receiver()

    def test_configuration_is_validated(self):
        with self.assertRaises(SystemExit):
            Events({"webhooks": [{"name": "x", "url": "file:///etc/passwd", "secret_env": "EDM_AI_TEST_HOOK"}]})
        with self.assertRaises(SystemExit):
            Events({"webhooks": [{"name": "x", "url": self.rx.url, "secret_env": "EDM_AI_TEST_HOOK", "events": ["doc.nope"]}]})
        os.environ["EDM_AI_TEST_HOOK_SHORT"] = "short"
        self.assertEqual(Events({"webhooks": [{"name": "x", "url": self.rx.url, "secret_env": "EDM_AI_TEST_HOOK_SHORT"}]}).hooks, [])     # weak secret = disabled


class EndToEnd(test_platform.DemoCase):
    def test_document_actions_produce_events(self):
        os.environ["EDM_AI_TEST_HOOK"] = HOOK
        rx = Receiver()
        try:
            self.app.events = Events({"webhooks": [{"name": "t", "url": rx.url, "secret_env": "EDM_AI_TEST_HOOK"}]}, timeout=2, wait=(0, 0.05))
            d, U = self.app.dms, self.app.users
            fid = d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]
            doc = d.register(U["kim"], "e.md", b"# e\n\nevent doc", folder_id=fid, doc_type="CT")
            d.submit(U["kim"], doc["id"], ["park"])
            d.approve(U["park"], doc["id"], "ok")
            self.assertTrue(wait(lambda: len(rx.got) >= 3))
            types = [h["x-edm-event"] for h, _ in rx.got]
            self.assertIn("doc.created", types)
            self.assertIn("approval.requested", types)
            self.assertIn("doc.approved", types)
        finally:
            self.app.events = Events({})
            rx.close()


if __name__ == "__main__":
    unittest.main()
