import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai import adapters, auth  # noqa: E402
from edm_ai.client import EdmApiError, EdmClient  # noqa: E402
from edm_ai.server import make_handler  # noqa: E402

SLACK, HOOK = "slack-signing-secret-0123456789", "webhook-secret-0123456789ab"


class Unit(unittest.TestCase):
    def test_slack_signature_roundtrip_expiry_and_tamper(self):
        body, ts = b"user_id=U1&text=hello", str(int(time.time()))
        sig = adapters.slack_sign(SLACK, ts, body)
        self.assertTrue(adapters.verify_slack(SLACK, ts, body, sig))
        self.assertFalse(adapters.verify_slack(SLACK, ts, body + b"x", sig))
        self.assertFalse(adapters.verify_slack("other-secret-0123456789abcdef", ts, body, sig))
        old = str(int(time.time()) - 3600)
        self.assertFalse(adapters.verify_slack(SLACK, old, body, adapters.slack_sign(SLACK, old, body)))   # replay window
        self.assertFalse(adapters.verify_slack(SLACK, "abc", body, sig))
        self.assertFalse(adapters.verify_slack(SLACK, ts, body, ""))

    def test_slack_signature_matches_documented_example(self):
        # Example from Slack's request-signing documentation.
        secret = "8f742231b10e8888abcd99yyyzzz85a5"
        ts = "1531420618"
        body = (b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&channel_id=G8PSS9T3V&"
                b"channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner&command=%2Fwebhook-collect&text=&"
                b"response_url=https%3A%2F%2Fhooks.slack.com%2Fcommands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&"
                b"trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c")
        self.assertEqual(adapters.slack_sign(secret, ts, body),
                         "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503")

    def test_replay_guard(self):
        g = adapters.ReplayGuard()
        self.assertTrue(g.fresh("a"))
        self.assertFalse(g.fresh("a"))
        self.assertTrue(g.fresh("b"))

    def test_format_chat_truncates(self):
        ans = {"answer": "가" * 5000, "evidence": []}
        self.assertLessEqual(len(adapters.format_chat(ans)), adapters.MAX_TEXT)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        os.environ.update(EDM_AI_SLACK_SIGNING_SECRET=SLACK, EDM_AI_WEBHOOK_SECRET=HOOK, EDM_AI_SECRET="t" * 32)
        cls.app.cfg["auth"], cls.app.cfg["rate_limit_per_min"] = {"mode": "token"}, 1000
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def post(self, path, body: bytes, headers):
        req = urllib.request.Request(self.base + path, body, headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def slack(self, user_id, text, ts=None, secret=SLACK):
        body = urllib.parse.urlencode({"user_id": user_id, "text": text, "team_id": "T1"}).encode()
        ts = ts or str(int(time.time()))
        return self.post("/api/adapters/slack", body, {"X-Slack-Request-Timestamp": ts,
                                                       "X-Slack-Signature": adapters.slack_sign(secret, ts, body)})

    def hook(self, ident, q, ts=None, secret=HOOK, raw=None):
        body = raw or json.dumps({"user": ident, "question": q}).encode()
        ts = ts or str(int(time.time()))
        return self.post("/api/adapters/webhook", body, {"X-EDM-Timestamp": ts, "X-EDM-Signature": adapters.webhook_sign(secret, ts, body)})

    # ---- slack ----
    def test_slack_answer_is_permission_scoped_and_ephemeral(self):
        code, fin = self.slack("U0PARK001", "PRJ-2024 원가 마진 이슈")
        self.assertEqual(code, 200)
        self.assertEqual(fin["response_type"], "ephemeral")
        self.assertIn("PRJ-2024_원가분석", fin["text"])
        _, sales = self.slack("U0KIM0001", "PRJ-2024 원가 마진 이슈")
        self.assertNotIn("원가분석", sales["text"])                    # same question, sales identity: finance doc excluded

    def test_slack_rejects_bad_signature_stale_timestamp_and_unknown_user(self):
        self.assertEqual(self.slack("U0KIM0001", "x", secret="wrong-secret-0123456789abcdef")[0], 401)
        self.assertEqual(self.slack("U0KIM0001", "x", ts=str(int(time.time()) - 4000))[0], 401)
        code, r = self.slack("U_UNKNOWN", "한빛전자 계약")
        self.assertEqual(code, 200)
        self.assertIn("연결되어 있지 않습니다", r["text"])             # never answered as someone else
        self.assertEqual(self.slack("U0KIM0001", "")[1]["text"][:4], "사용법:")

    def test_spoofed_user_header_does_not_authenticate_adapter(self):
        body = urllib.parse.urlencode({"user_id": "U0PARK001", "text": "한빛전자"}).encode()
        code, _ = self.post("/api/adapters/slack", body, {"X-User-Id": "admin"})
        self.assertEqual(code, 401)

    # ---- webhook ----
    def test_webhook_by_email_identity(self):
        code, r = self.hook({"type": "email", "id": "park@example.co.kr"}, "고객사별 계약금액 합계")
        self.assertEqual(code, 200)
        self.assertIn("850,000,000", r["text"])
        self.assertTrue(r["evidence"])

    def test_webhook_security(self):
        ident = {"type": "email", "id": "kim@example.co.kr"}
        self.assertEqual(self.hook(ident, "x", secret="wrong-secret-0123456789abcdef")[0], 401)
        self.assertEqual(self.hook(ident, "x", ts=str(int(time.time()) - 4000))[0], 401)
        self.assertEqual(self.hook({"type": "email", "id": "nobody@x.com"}, "한빛전자")[0], 403)
        self.assertEqual(self.hook(ident, "x", raw=b"not json")[0], 400)
        ts = str(int(time.time()))
        body = json.dumps({"user": ident, "question": "한빛전자 계약"}).encode()
        h = {"X-EDM-Timestamp": ts, "X-EDM-Signature": adapters.webhook_sign(HOOK, ts, body)}
        self.assertEqual(self.post("/api/adapters/webhook", body, h)[0], 200)
        self.assertEqual(self.post("/api/adapters/webhook", body, h)[0], 409)      # exact replay refused

    def test_adapters_disabled_without_secret(self):
        saved = os.environ.pop("EDM_AI_WEBHOOK_SECRET")
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, False))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/adapters/webhook", b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 404)
            srv.shutdown()
            srv.server_close()
        finally:
            os.environ["EDM_AI_WEBHOOK_SECRET"] = saved

    def test_weak_secret_refused_at_startup(self):
        os.environ["EDM_AI_WEBHOOK_SECRET"] = "short"
        try:
            with self.assertRaises(SystemExit):
                make_handler(self.app, False)
        finally:
            os.environ["EDM_AI_WEBHOOK_SECRET"] = HOOK

    # ---- python client ----
    def test_client_against_real_server(self):
        c = EdmClient(self.base, token=auth.issue("t" * 32, "kim"))
        ans = c.ask("한빛전자 계약 금액")
        self.assertTrue(ans["evidence"])
        self.assertTrue(c.docs("지체상금"))
        with self.assertRaises(EdmApiError) as cm:
            EdmClient(self.base, token="bad").ask("x")
        self.assertEqual(cm.exception.status, 401)
        f = Path(self.tmp.name) / "client_up.md"
        f.write_text("# 클라이언트\n\n클라이언트 업로드 본문입니다 클라이언트토큰.", "utf-8")
        d = c.upload(str(f))
        self.assertEqual(d["source"], "uploads/client_up.md")
        self.assertEqual(c.delete(d["id"])["deleted"], "uploads/client_up.md")
        self.assertEqual(c.alerts(), [])


if __name__ == "__main__":
    unittest.main()
