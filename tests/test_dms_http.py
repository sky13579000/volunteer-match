import base64
import io
import json
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.routes import build_routes  # noqa: E402
from edm_ai.server import make_handler  # noqa: E402


def b64(text):
    return base64.b64encode(text.encode() if isinstance(text, str) else text).decode()


class HttpBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.app.cfg["auth"], cls.app.cfg["rate_limit_per_min"] = {"mode": "header"}, 100000
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, uid=None, body=None, raw=False, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"X-User-Id": uid} if uid else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data, h, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                payload = r.read()
                is_json = r.headers.get("Content-Type", "").startswith("application/json")
                return r.status, (json.loads(payload or b"{}") if is_json and not raw else payload), r.headers
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload or b"{}"), e.headers
            except ValueError:
                return e.code, payload, e.headers

    def reg(self, uid="kim", name="h.md", text="# HTTP\n\nHTTP 시험 본문입니다.", **meta):
        code, d, _ = self.call("POST", "/api/docs", uid, {"filename": name, "content_b64": b64(text), **meta})
        self.assertEqual(code, 200, d)
        return d


class Lifecycle(HttpBase):
    def test_register_search_version_diff_download(self):
        d = self.reg(doc_type="GEN", tags=["http"], title="HTTP 문서", text="# HTTP\n\n첫 버전 httpprobe.")
        self.assertRegex(d["doc_no"], r"^GEN-")
        code, s, _ = self.call("GET", "/api/docs?q=httpprobe&type=GEN&tag=http", "kim")
        self.assertEqual((code, [x["id"] for x in s["items"]]), (200, [d["id"]]))
        code, v2, _ = self.call("POST", f"/api/docs/{d['id']}/versions", "kim", {"filename": "h.md", "content_b64": b64("# HTTP\n\n둘째 버전 httpprobe 추가."), "comment": "수정"})
        self.assertEqual(v2["current_ver"], 2)
        code, vers, _ = self.call("GET", f"/api/docs/{d['id']}/versions", "kim")
        self.assertEqual([v["ver"] for v in vers], [2, 1])
        code, diff, _ = self.call("GET", f"/api/docs/{d['id']}/diff?a=1&b=2", "kim")
        self.assertGreater(diff["added"], 0)
        code, data, h = self.call("GET", f"/api/docs/{d['id']}/download", "kim", raw=True)
        self.assertEqual(code, 200)
        self.assertIn("둘째 버전", data.decode())
        self.assertTrue(h["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        code, old, _ = self.call("GET", f"/api/docs/{d['id']}/download?ver=1", "kim", raw=True)
        self.assertIn("첫 버전", old.decode())
        self.assertEqual(self.call("GET", f"/api/docs/{d['id']}/download", "choi")[0], 404)

    def test_uploaded_html_is_never_rendered_inline(self):
        d = self.reg(name="evil.html", text="<html><script>alert(1)</script><p>본문 htmlprobe</p></html>")
        code, data, h = self.call("GET", f"/api/docs/{d['id']}/download", "kim", raw=True)
        self.assertEqual(h["Content-Type"], "application/octet-stream")
        self.assertTrue(h["Content-Disposition"].startswith("attachment;"))
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertNotIn("<script>", self.call("GET", f"/api/docs/{d['id']}", "kim")[1]["text"])    # text view is extracted text only

    def test_approval_flow_over_http(self):
        d = self.reg(doc_type="CT", text="# 계약\n\nCT-2024-001 계약 approvalhttp.")
        self.assertEqual(self.call("POST", f"/api/docs/{d['id']}/submit", "kim", {"approvers": ["park"]})[1]["workflow"]["status"], "pending")
        code, tasks, _ = self.call("GET", "/api/dms/tasks", "park")
        self.assertIn(d["id"], [t["doc_id"] for t in tasks["tasks"]])
        self.assertEqual(self.call("POST", f"/api/docs/{d['id']}/approve", "kim", {})[0], 404)          # requester cannot
        code, out, _ = self.call("POST", f"/api/docs/{d['id']}/approve", "park", {"comment": "OK"})
        self.assertEqual((code, out["status"]), (200, "approved"))
        code, n, _ = self.call("GET", "/api/dms/notifications?unread=1", "kim")
        self.assertTrue([x for x in n["items"] if x["kind"] == "approved"])
        self.assertEqual(self.call("POST", "/api/dms/notifications/read", "kim", {})[1]["unread"], 0)

    def test_comments_favorites_trash(self):
        d = self.reg(text="# 협업\n\n협업 http 시험.")
        self.assertEqual(self.call("POST", f"/api/docs/{d['id']}/comments", "kim", {"text": "댓글"})[0], 200)
        self.assertEqual(self.call("POST", f"/api/docs/{d['id']}/comments", "choi", {"text": "침입"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/docs/{d['id']}/favorite", "kim", {"on": True})[1], {"favorite": True})
        self.assertEqual([x["id"] for x in self.call("GET", "/api/dms/favorites", "kim")[1]][-1], d["id"])
        self.assertEqual(self.call("DELETE", f"/api/docs/{d['id']}", "kim")[0], 200)
        self.assertEqual(self.call("GET", f"/api/docs/{d['id']}", "kim")[0], 404)
        self.assertIn(d["id"], [t["id"] for t in self.call("GET", "/api/dms/trash", "kim")[1]])
        self.assertEqual(self.call("POST", f"/api/docs/{d['id']}/trash/restore", "kim", {})[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/docs/{d['id']}", "kim")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/docs/{d['id']}/purge", "kim")[0], 403)             # only admins purge

    def test_admin_endpoints_are_admin_only(self):
        for path in ("/api/dms/admin/audit", "/api/dms/admin/audit.csv", "/api/dms/admin/stats"):
            self.assertEqual(self.call("GET", path, "kim")[0], 403, path)
            self.assertEqual(self.call("GET", path, "admin")[0], 200, path)
        self.assertEqual(self.call("POST", "/api/dms/admin/maintenance", "kim", {})[0], 403)
        self.assertEqual(self.call("PUT", "/api/dms/admin/doctypes", "kim", {"code": "ZZ", "name": "x"})[0], 403)
        code, csv_bytes, h = self.call("GET", "/api/dms/admin/audit.csv", "admin", raw=True)
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(h["Content-Type"], "text/csv")

    def test_folders_and_bulk_export(self):
        f = self.call("POST", "/api/dms/folders", "admin", {"parent_id": None, "name": "HTTP폴더", "entries": [{"type": "dept", "id": "sales", "role": "editor"}]})[1]
        a = self.reg(folder_id=f["id"], text="# a\n\nbulk http a.")
        b = self.reg(folder_id=f["id"], text="# b\n\nbulk http b.")
        tree = self.call("GET", "/api/dms/folders", "kim")[1]
        self.assertTrue([x for x in tree if x["id"] == f["id"] and x["doc_count"] == 2])
        self.assertFalse([x for x in self.call("GET", "/api/dms/folders", "choi")[1] if x["id"] == f["id"]])      # hidden from outsiders
        res = self.call("POST", "/api/dms/bulk", "kim", {"action": "add_tags", "ids": [a["id"], b["id"], 999999], "params": {"tags": ["일괄"]}})[1]
        self.assertEqual((res["ok"], res["failed"]), (2, 1))
        code, z, h = self.call("POST", "/api/dms/export", "kim", {"ids": [a["id"], b["id"]]}, raw=True)
        self.assertEqual(h["Content-Type"], "application/zip")
        self.assertTrue([n for n in zipfile.ZipFile(io.BytesIO(z)).namelist() if n.endswith("h.md")])
        self.assertEqual(self.call("DELETE", f"/api/dms/folders/{f['id']}", "admin")[0], 409)                    # not empty


class Hardening(HttpBase):
    def test_every_authenticated_route_rejects_anonymous(self):
        samples = {r"(\d+)": "1", r"\d+": "1"}
        for method, rx, _fn, _mut in build_routes(self.app):
            path = re.sub(r"\(\\d\+\)", "1", rx.pattern).replace("\\.", ".")
            code, _, _ = self.call(method, path, None, {} if method != "GET" else None)
            self.assertEqual(code, 401, f"{method} {path}")

    def test_oversized_and_malformed_bodies(self):
        h = {"Content-Length": str(500 * 1024 * 1024)}
        req = urllib.request.Request(self.base + "/api/dms/bulk", b"{}", {"X-User-Id": "kim", **h}, method="POST")
        with self.assertRaises((urllib.error.HTTPError, urllib.error.URLError, ConnectionError)):
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(self.call("POST", "/api/docs", "kim", {"filename": "x.md", "content_b64": "###"})[0], 400)
        req = urllib.request.Request(self.base + "/api/docs", b"not json", {"X-User-Id": "kim"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.call("GET", "/api/nothing", "kim")[0], 404)
        self.assertEqual(self.call("PATCH", "/api/docs/1", "kim", {})[0], 501)               # unsupported method is refused, not mis-routed

    def test_concurrent_registrations_and_searches(self):
        errs, ids = [], []

        def worker(i):
            try:
                code, d, _ = self.call("POST", "/api/docs", "kim", {"filename": f"c{i}.md", "content_b64": b64(f"# 동시 {i}\n\n동시성 시험 concurrentprobe{i}.")})
                if code != 200:
                    errs.append((i, code, d))
                else:
                    ids.append(d["doc_no"])
                self.call("GET", "/api/docs?q=concurrentprobe", "kim")
            except Exception as e:  # noqa: BLE001
                errs.append((i, repr(e)))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len(set(ids)), 16)                                         # numbering never hands out duplicates


class PublicShare(HttpBase):
    def mk(self, **kw):
        d = self.reg(text="# 공개\n\n외부 공유 시험 sharehttp. 연락처 010-9999-8888.")
        code, s, _ = self.call("POST", f"/api/docs/{d['id']}/shares", "kim", {"days": 2, **kw})
        self.assertEqual(code, 200, s)
        return d, s

    def test_public_page_and_info_without_login(self):
        d, s = self.mk()
        code, page, h = self.call("GET", s["path"], None, raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"share.js", page)
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        code, info, _ = self.call("GET", "/api/share/" + s["token"], None)
        self.assertEqual((code, info["title"]), (200, d["title"]))
        self.assertNotIn("9999", info["text"])
        self.assertEqual(self.call("GET", "/api/share/" + "x" * 30, None)[0], 404)
        self.assertEqual(self.call("GET", "/api/docs", None)[0], 401)               # the token grants access to ONE document only

    def test_password_and_download_over_http(self):
        d, s = self.mk(password="secret-pass", perm="download", max_downloads=1)
        self.assertEqual(self.call("GET", "/api/share/" + s["token"], None)[1], {"requires_password": True})
        self.assertEqual(self.call("POST", f"/api/share/{s['token']}/access", None, {"password": "nope"})[0], 403)
        self.assertEqual(self.call("POST", f"/api/share/{s['token']}/access", None, {"password": "secret-pass"})[1]["title"], d["title"])
        code, data, h = self.call("POST", f"/api/share/{s['token']}/download", None, {"password": "secret-pass"}, raw=True)
        self.assertEqual(code, 200)
        self.assertTrue(h["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(self.call("POST", f"/api/share/{s['token']}/download", None, {"password": "secret-pass"})[0], 403)

    def test_share_management_requires_permission_and_revocation_works(self):
        d, s = self.mk()
        self.assertEqual(self.call("GET", f"/api/docs/{d['id']}/shares", "choi")[0], 404)
        self.assertEqual(self.call("DELETE", f"/api/docs/{d['id']}/shares/{s['id']}", "kim")[0], 200)
        self.assertEqual(self.call("GET", "/api/share/" + s["token"], None)[0], 404)
        listed = self.call("GET", f"/api/docs/{d['id']}/shares", "kim")[1]
        self.assertNotIn(s["token"], json.dumps(listed))                              # the token is shown exactly once

    def test_ip_rate_limit_on_public_endpoints(self):
        # own server => own rate-limit buckets, so this test cannot starve the others
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, False))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            codes = []
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/share/" + "y" * 30)
                except urllib.error.HTTPError as e:
                    codes.append(e.code)
            self.assertEqual(codes[0], 404)
            self.assertEqual(codes[-1], 429)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
