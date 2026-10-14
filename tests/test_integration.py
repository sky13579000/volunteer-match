import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai import adapters, directory  # noqa: E402
from edm_ai.server import make_handler  # noqa: E402

GW = "groupware-secret-0123456789ab"
MAP = {
    "fields": {"id": "사번", "name": "이름", "dept": "부서", "title": "직급", "groups": "그룹", "email": "메일"},
    "dept_map": {"MICE유치팀": "mice", "경영지원팀": "finance", "인사팀": "hr"},
    "group_roles": {"재무결재": "finance", "인사": "hr", "전체관리": "admin"},
    "title_levels": {"팀장": 3, "과장": 2, "사원": 1, "임원": 9},
    "preserve": [],
    "ignore_inactive": {"field": "상태", "values": ["퇴직"]},
}
CSV = ("사번,이름,부서,직급,그룹,메일,상태\n"
       "kim,김유치,MICE유치팀,팀장,,Kim@Iccjeju.example,재직\n"
       "han,한신규,경영지원팀,과장,재무결재;전체관리,han@iccjeju.example,재직\n"
       "oh,오퇴직,인사팀,사원,,oh@iccjeju.example,퇴직\n"
       "bad id,공백아이디,인사팀,사원,,x@iccjeju.example,재직\n"
       "yoo,유외부,알수없는부서,임원,,yoo@iccjeju.example,재직\n")


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name)
        self.users = self.d / "users.json"
        self.users.write_text(json.dumps([
            {"id": "admin", "name": "관리자", "dept": "it", "roles": ["admin"], "level": 99},
            {"id": "kim", "name": "김옛", "dept": "sales", "roles": [], "level": 2, "external": {"slack": "U1"}},
            {"id": "gone", "name": "퇴사예정", "dept": "hr", "roles": [], "level": 1}]), "utf-8")
        self.map = self.d / "map.json"
        self.map.write_text(json.dumps(MAP, ensure_ascii=False), "utf-8")
        self.csv = self.d / "u.csv"
        self.csv.write_text(CSV, "utf-8-sig")

    def tearDown(self):
        self.t.cleanup()

    def run_sync(self, **kw):
        return directory.sync(str(self.csv), self.users, str(self.map), **kw)

    def test_dry_run_changes_nothing_and_reports_diff(self):
        before = self.users.read_bytes()
        r = self.run_sync()
        self.assertFalse(r["applied"])
        self.assertEqual(self.users.read_bytes(), before)
        self.assertEqual(r["added"], ["han", "yoo"])
        self.assertEqual([c["id"] for c in r["changed"]], ["kim"])
        self.assertTrue(any("invalid or missing id" in w for w in r["warnings"]))        # 'bad id'
        self.assertTrue(any("not in dept_map" in w for w in r["warnings"]))
        self.assertTrue(any("'admin' role cannot be granted" in w for w in r["warnings"]))
        self.assertTrue(any("capped" in w for w in r["warnings"]))                        # 임원 -> level 9 > max_level 3

    def test_apply_keeps_admin_unmapped_dept_is_not_privileged_and_email_lowercased(self):
        self.run_sync(apply=True)
        users = {u["id"]: u for u in json.loads(self.users.read_text("utf-8"))}
        self.assertEqual(users["kim"]["dept"], "mice")
        self.assertEqual(users["kim"]["email"], "kim@iccjeju.example")
        self.assertEqual(users["kim"]["external"]["slack"], "U1")           # fields the source does not manage survive
        self.assertEqual(users["han"]["roles"], ["finance"])                 # admin is never granted by sync
        self.assertEqual((users["yoo"]["dept"], users["yoo"]["level"]), ("external", 3))
        self.assertNotIn("oh", users)                                        # inactive
        self.assertIn("gone", users)                                         # not pruned without --prune
        self.assertTrue((self.d / "users.json.bak").exists())

    def test_prune_removes_missing_users_but_never_admins(self):
        r = self.run_sync(apply=True, prune=True)
        ids = {u["id"] for u in json.loads(self.users.read_text("utf-8"))}
        self.assertIn("admin", ids)
        self.assertNotIn("gone", ids)
        self.assertEqual(r["removed"], ["gone"])

    def test_source_cannot_overwrite_an_admin_account(self):
        self.csv.write_text("사번,이름,부서,직급,그룹,메일,상태\nadmin,가짜,인사팀,사원,,a@x.example,재직\n", "utf-8")
        r = self.run_sync(apply=True)
        self.assertEqual(r["protected_skipped"], ["admin"])
        admin = next(u for u in json.loads(self.users.read_text("utf-8")) if u["id"] == "admin")
        self.assertEqual((admin["name"], admin["level"]), ("관리자", 99))

    def test_refuses_result_without_admin_and_duplicate_emails(self):
        with self.assertRaises(directory.DirectoryError):
            directory.validate([{"id": "a", "roles": []}])
        with self.assertRaises(directory.DirectoryError):
            directory.validate([{"id": "a", "roles": ["admin"], "email": "x@y"}, {"id": "b", "roles": [], "email": "x@y"}])

    def test_cp949_csv_from_korean_excel(self):
        self.csv.write_bytes(CSV.encode("cp949"))
        self.assertEqual(self.run_sync()["added"], ["han", "yoo"])

    def test_ldif_with_folding_base64_and_multivalued_groups(self):
        ldif = ("dn: uid=han,ou=people,dc=iccjeju\nuid: han\ncn:: " + base64.b64encode("한신규".encode()).decode() + "\n"
                "departmentNumber: 경영지원팀\ntitle: 과장\nmail: han@iccjeju.\n example\nmemberOf: 재무결재\nmemberOf: 인사\n\n"
                "dn: uid=kim,ou=people,dc=iccjeju\nuid: kim\ncn: 김유치\ndepartmentNumber: MICE유치팀\ntitle: 팀장\nmail: kim@iccjeju.example\n")
        (self.d / "u.ldif").write_text(ldif, "utf-8")
        m = json.loads(self.map.read_text("utf-8"))
        m["fields"] = {"id": "uid", "name": "cn", "dept": "departmentNumber", "title": "title", "groups": "memberOf", "email": "mail"}
        self.map.write_text(json.dumps(m, ensure_ascii=False), "utf-8")
        r = directory.sync(str(self.d / "u.ldif"), self.users, str(self.map), apply=True)
        users = {u["id"]: u for u in json.loads(self.users.read_text("utf-8"))}
        self.assertEqual(users["han"]["name"], "한신규")
        self.assertEqual(users["han"]["email"], "han@iccjeju.example")       # folded line re-joined
        self.assertEqual(users["han"]["roles"], ["finance", "hr"])
        self.assertEqual(r["added"], ["han"])

    def test_unknown_mapping_key_is_rejected(self):
        self.map.write_text('{"typo_key": 1}', "utf-8")
        with self.assertRaises(directory.DirectoryError):
            self.run_sync()


class HotReloadTest(test_platform.DemoCase):
    def test_users_json_change_is_picked_up_without_restart(self):
        f = self.app._users_file
        users = json.loads(f.read_text("utf-8"))
        users.append({"id": "newbie", "name": "신입", "dept": "mice", "roles": [], "level": 1, "email": "NewBie@iccjeju.example"})
        f.write_text(json.dumps(users, ensure_ascii=False), "utf-8")
        os.utime(f, (time.time() + 5, time.time() + 5))
        self.assertNotIn("newbie", self.app.users)
        self.app.refresh_users(every=0)
        self.assertIn("newbie", self.app.users)
        self.assertEqual(self.app.external[("email", "newbie@iccjeju.example")], "newbie")      # e-mail match is case-insensitive

    def test_broken_users_json_keeps_previous_list(self):
        f = self.app._users_file
        good = f.read_text("utf-8")
        f.write_text("{not json", "utf-8")
        os.utime(f, (time.time() + 9, time.time() + 9))
        self.app.refresh_users(every=0)
        self.assertIn("kim", self.app.users)
        f.write_text(good, "utf-8")


class Base(test_platform.DemoCase):
    auth = {"mode": "token"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ.update(EDM_AI_GW_SECRET=GW, EDM_AI_SECRET="t" * 32)
        cls.app.cfg["auth"], cls.app.cfg["rate_limit_per_min"] = cls.auth, 1000
        cls.app.cfg.setdefault("adapters", {})["groupware"] = {
            "secret_env": "EDM_AI_GW_SECRET", "system": "gw", "max_mb": 5,
            "routes": [{"form": "계약품의", "folder": "MICE유치팀", "doc_type": "CT", "tags": ["그룹웨어"]}],
            "default_route": {"folder": "경영지원팀", "doc_type": "CT"}}
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def req(self, path, body=None, headers=None, method="GET"):
        r = urllib.request.Request(self.base + path, body, headers or {}, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


class GroupwareTest(Base):
    def send(self, payload, secret=GW, ts=None, raw=None):
        body = raw or json.dumps(payload, ensure_ascii=False).encode()
        ts = ts or str(int(time.time()))
        return self.req("/api/adapters/groupware", body, {"X-EDM-Timestamp": ts, "X-EDM-Signature": adapters.webhook_sign(secret, ts, body)}, "POST")

    def payload(self, **kw):
        p = {"doc_id": "APPR-2026-00123", "form": "계약품의", "title": "그룹웨어 계약 품의서",
             "owner": {"type": "email", "id": "KIM@iccjeju.example"},
             "files": [{"filename": "품의서.md", "content_b64": b64("# 품의서\n\n제주관광공사 대관 계약 품의. 그룹웨어결재유니크.")}],
             "approved": True, "approval_line": [{"name": "박재정", "title": "팀장", "result": "승인", "at": "2026-10-01 10:00"}]}
        p.update(kw)
        return p

    def test_register_idempotent_update_and_approval(self):
        c, r = self.send(self.payload())
        self.assertEqual((c, r["results"][0]["status"]), (200, "created"))
        did = r["results"][0]["id"]
        row = self.app.dms.row(did)
        self.assertEqual((row["owner"], row["status"], row["doc_type"]), ("kim", "approved", "CT"))      # registered AS the drafter
        self.assertEqual(row["props"]["gw_key"], "gw:APPR-2026-00123")
        self.assertIn("그룹웨어", row["tags"])
        # the content is searchable by the drafter and invisible to an outsider (permissions are the same as for people)
        self.assertTrue(any(h.ref.startswith("dms/") for h in self.ask("kim", "그룹웨어결재유니크").evidence))
        self.assertFalse(any("그룹웨어결재유니크" in e.text for e in self.ask("choi", "그룹웨어결재유니크").evidence))
        # same message again -> no duplicate
        c, r2 = self.send(self.payload(), ts=str(int(time.time()) + 1))      # (an identical signature would be refused as a replay)
        self.assertEqual(r2["results"][0]["status"], "unchanged")
        self.assertEqual(r2["results"][0]["id"], did)
        # new content with the same doc_id -> new version of the same document
        p = self.payload(files=[{"filename": "품의서.md", "content_b64": b64("# 품의서 개정\n\n금액 변경.")}])
        c, r3 = self.send(p, ts=str(int(time.time()) + 2))
        self.assertEqual((r3["results"][0]["status"], self.app.dms.row(did)["current_ver"]), ("updated", 2))

    def test_multiple_files_are_registered_and_linked(self):
        files = [{"filename": f"f{i}.md", "content_b64": b64(f"# 첨부{i}\n\n내용{i} 다중첨부유니크")} for i in range(3)]
        c, r = self.send(self.payload(doc_id="APPR-MULTI", files=files))
        self.assertEqual((c, [x["status"] for x in r["results"]]), (200, ["created"] * 3))
        rel = self.app.dms.related(self.app.users["kim"], r["results"][0]["id"])
        self.assertEqual({x["id"] for x in rel}, {r["results"][1]["id"], r["results"][2]["id"]})

    def test_default_route_and_folder_permission_are_enforced(self):
        # 정도청 (provincial dept) cannot even see the MICE유치팀 folder -> the groupware cannot sneak documents in
        c, r = self.send(self.payload(doc_id="APPR-DENY", owner={"type": "email", "id": "jung@iccjeju.example"}))
        self.assertEqual(c, 207)
        self.assertEqual(r["results"][0]["status"], "error")
        self.assertIn(r["results"][0]["code"], (403, 404))
        c, r = self.send(self.payload(doc_id="APPR-DEF", form="기타양식", owner={"type": "email", "id": "park@iccjeju.example"}, approved=False))
        self.assertEqual(r["results"][0]["status"], "created")
        self.assertEqual(self.app.dms.row(r["results"][0]["id"])["status"], "draft")        # approved=false is not promoted

    def test_rejections(self):
        self.assertEqual(self.send(self.payload(), secret="wrong-secret-0123456789abcdef")[0], 401)
        self.assertEqual(self.send(self.payload(), ts=str(int(time.time()) - 3600))[0], 401)
        self.assertEqual(self.send(self.payload(owner={"type": "email", "id": "nobody@x.example"}))[0], 403)
        self.assertEqual(self.send(self.payload(doc_id=""))[0], 400)
        self.assertEqual(self.send(self.payload(files=[]))[0], 400)
        c, _ = self.send(None, raw=b"not json")
        self.assertEqual(c, 400)
        self.app.cfg["adapters"]["groupware"]["routes"].append({"form": "유령", "folder": "없는폴더"})
        self.assertEqual(self.send(self.payload(form="유령"))[0], 422)
        c, r = self.send(self.payload(doc_id="APPR-BADB64", files=[{"filename": "a.md", "content_b64": "***"}]))
        self.assertEqual((c, r["results"][0]["status"]), (207, "error"))

    def test_replayed_message_is_refused(self):
        body = json.dumps(self.payload(doc_id="APPR-REPLAY"), ensure_ascii=False).encode()
        ts = str(int(time.time()))
        h = {"X-EDM-Timestamp": ts, "X-EDM-Signature": adapters.webhook_sign(GW, ts, body)}
        self.assertEqual(self.req("/api/adapters/groupware", body, h, "POST")[0], 200)
        self.assertEqual(self.req("/api/adapters/groupware", body, h, "POST")[0], 409)


class ProxyAuthTest(Base):
    auth = {"mode": "proxy", "header": "X-Forwarded-Email", "match": "email", "trusted_proxies": ["127.0.0.1"], "logout_url": "/oauth2/sign_out"}

    def test_email_identity_from_trusted_proxy(self):
        c, me = self.req("/api/me", headers={"X-Forwarded-Email": "Park@Iccjeju.Example"})
        self.assertEqual((c, me["id"]), (200, "park"))
        self.assertEqual(self.req("/api/me", headers={"X-Forwarded-Email": "stranger@evil.example"})[0], 401)
        self.assertEqual(self.req("/api/me")[0], 401)
        self.assertEqual(self.req("/api/me", headers={"X-User-Id": "admin"})[0], 401)          # the old header is NOT an identity here

    def test_config_exposes_mode_and_logout(self):
        c, cfg = self.req("/api/config")
        self.assertEqual((cfg["auth_mode"], cfg["logout_url"], cfg["demo"]), ("proxy", "/oauth2/sign_out", False))

    def test_untrusted_source_address_is_refused(self):
        self.app.cfg["auth"]["trusted_proxies"] = ["10.9.9.9"]
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, False))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/me", headers={"X-Forwarded-Email": "park@iccjeju.example"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(r)
            self.assertEqual(cm.exception.code, 401)
        finally:
            srv.shutdown()
            srv.server_close()
            self.app.cfg["auth"]["trusted_proxies"] = ["127.0.0.1"]


if __name__ == "__main__":
    unittest.main()
