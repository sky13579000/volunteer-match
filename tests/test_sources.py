import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeservers  # noqa: E402
import test_platform  # noqa: E402

from edm_ai.config import App  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


def make_app(sources, tmp: Path):
    shutil.copytree(test_platform.DEMO / "docs", tmp / "docs")
    shutil.copy(test_platform.DEMO / "users.json", tmp / "users.json")
    cfg = json.loads((test_platform.DEMO / "config.json").read_text("utf-8"))
    cfg["store"], cfg["structured"] = "store.db", None
    cfg.pop("entities", None)
    cfg["sources"] = sources
    (tmp / "config.json").write_text(json.dumps(cfg), "utf-8")
    return App(str(tmp / "config.json"))


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.tmp = Path(self.t.name)
        self.nas = self.tmp / "nas"
        (self.nas / "계약").mkdir(parents=True)
        (self.nas / "인사").mkdir()
        (self.nas / "계약" / "대관계약.md").write_text("# 대관 계약\n\nNAS대관계약본문유니크 계약금액은 3억원이다.", "utf-8")
        (self.nas / "인사" / "평가.md").write_text("# 평가\n\nNAS인사평가본문유니크", "utf-8")
        (self.nas / "~$임시.md").write_text("temp", "utf-8")
        (self.nas / "메모.md").write_text("# 메모\n\nNAS메모본문유니크", "utf-8")

    def tearDown(self):
        self.t.cleanup()

    def ids(self, app, uid, q):
        return {e.ref.split("#")[0] for e in app.engine.ask(app.users[uid], q).evidence}


class Reference(Base):
    def app(self, **extra):
        src = {"name": "본부NAS", "type": "path", "path": str(self.nas), "mode": "reference", "acl": {"depts": ["mice", "finance"], "level": 1},
               "acl_rules": [{"glob": "인사/*", "acl": {"roles": ["hr"], "level": 3}}], **extra}
        a = make_app([src], self.tmp / "w")
        a.ingest()
        return a

    def test_files_are_searchable_with_the_configured_acl_and_hidden_ones_stay_hidden(self):
        a = self.app()
        self.assertIn("본부NAS/계약/대관계약.md", self.ids(a, "kim", "NAS대관계약본문유니크"))
        self.assertNotIn("본부NAS/인사/평가.md", self.ids(a, "kim", "NAS인사평가본문유니크"))                # acl_rules: HR only
        self.assertIn("본부NAS/인사/평가.md", self.ids(a, "lee", "NAS인사평가본문유니크"))
        self.assertNotIn("본부NAS/계약/대관계약.md", self.ids(a, "choi", "NAS대관계약본문유니크"))              # outsiders see nothing
        self.assertNotIn("본부NAS/~$임시.md", {r["source"] for r in a.dms.q("SELECT source FROM dms_docs")})   # exclude pattern

    def test_documents_are_listed_under_external_sources_and_download_the_live_file(self):
        a = self.app()
        row = a.dms.q1("SELECT id FROM dms_docs WHERE source='본부NAS/계약/대관계약.md'")
        self.assertEqual(a.dms.get(a.users["kim"], row["id"])["folder_path"], "외부 소스 / 본부NAS / 계약")
        (self.nas / "계약" / "대관계약.md").write_text("# 대관 계약\n\n수정된 본문", "utf-8")
        name, data, _ = a.dms.download(a.users["kim"], row["id"])
        self.assertIn("수정된 본문", data.decode())                                                         # the file as it is NOW

    def test_changes_additions_and_deletions_follow_the_source(self):
        a = self.app()
        (self.nas / "새문서.md").write_text("# 새\n\nNAS새문서유니크", "utf-8")
        (self.nas / "메모.md").unlink()
        st = a.sources.sync("본부NAS")
        self.assertEqual(st.get("added"), 1)
        self.assertIn("본부NAS/새문서.md", self.ids(a, "kim", "NAS새문서유니크"))
        self.assertNotIn("본부NAS/메모.md", self.ids(a, "kim", "NAS메모본문유니크"))
        self.assertFalse(a.dms.q("SELECT 1 FROM dms_docs WHERE source='본부NAS/메모.md'"))

    def test_dry_run_changes_nothing(self):
        a = self.app()
        (self.nas / "드라이.md").write_text("# 드라이\n\n드라이런유니크", "utf-8")
        st = a.sources.sync("본부NAS", dry_run=True)
        self.assertEqual(st["added"], 1)
        self.assertNotIn("본부NAS/드라이.md", self.ids(a, "kim", "드라이런유니크"))

    def test_offline_nas_never_empties_the_index(self):
        a = self.app()
        shutil.move(str(self.nas), str(self.tmp / "nas_offline"))
        st = a.ingest()
        self.assertEqual(st["pruned"], 0)
        self.assertIn("접근할 수 없습니다", st["roots"]["본부NAS/"]["error"])
        self.assertIn("본부NAS/계약/대관계약.md", self.ids(a, "kim", "NAS대관계약본문유니크"))

    def test_size_limit_and_status(self):
        (self.nas / "큰파일.md").write_text("# 큰\n\n" + "가" * 2_000_000, "utf-8")
        a = self.app(max_mb=1)
        self.assertFalse(a.dms.q("SELECT 1 FROM dms_docs WHERE source='본부NAS/큰파일.md'"))
        st = a.sources.status()[0]
        self.assertEqual((st["name"], st["mode"], st["documents"] >= 3), ("본부NAS", "reference", True))

    def test_default_acl_is_admin_only(self):
        a = make_app([{"name": "비공개", "type": "path", "path": str(self.nas), "mode": "reference"}], self.tmp / "w2")
        a.ingest()
        self.assertNotIn("비공개/계약/대관계약.md", self.ids(a, "kim", "NAS대관계약본문유니크"))
        self.assertIn("비공개/계약/대관계약.md", self.ids(a, "admin", "NAS대관계약본문유니크"))

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(SystemExit):
            make_app([{"name": "../x", "type": "path", "path": "/"}], self.tmp / "w3")
        with self.assertRaises(SystemExit):
            make_app([{"name": "x", "type": "ftp"}], self.tmp / "w4")


class Migrate(Base):
    def app(self, **extra):
        src = {"name": "이관NAS", "type": "path", "path": str(self.nas), "mode": "migrate", "folder": "NAS이관", "owner": "admin", "doc_type": "GEN",
               "folder_entries": [{"type": "dept", "id": "mice", "role": "editor"}], **extra}
        return make_app([src], self.tmp / "w")

    def test_files_become_managed_documents_with_folder_tree_and_provenance(self):
        a = self.app()
        st = a.sources.sync("이관NAS")
        self.assertEqual((st["migrated"], st["error"], st["excluded"]), (3, 0, 1))
        d = a.dms
        row = d.q1("SELECT id,folder_id FROM dms_docs WHERE title LIKE '%대관 계약%' AND origin='managed'")
        got = d.get(a.users["admin"], row["id"])
        self.assertEqual(got["folder_path"], "NAS이관 / 계약")
        self.assertEqual(got["props"]["이관원본"], "이관NAS:계약/대관계약.md")
        self.assertIn("이관", got["tags"])

    def test_rerun_is_incremental_new_versions_and_duplicates(self):
        a = self.app()
        a.sources.sync("이관NAS")
        self.assertEqual(a.sources.sync("이관NAS")["unchanged"], 3)
        (self.nas / "계약" / "대관계약.md").write_text("# 대관 계약\n\n개정된 NAS본문", "utf-8")
        shutil.copy(self.nas / "메모.md", self.nas / "메모_복사본.md")
        st = a.sources.sync("이관NAS")
        self.assertEqual((st["updated"], st["duplicate"], st["migrated"]), (1, 1, 0))
        row = a.dms.q1("SELECT id,current_ver FROM dms_docs WHERE title LIKE '%대관 계약%'")
        self.assertEqual(row["current_ver"], 2)

    def test_source_deletion_is_flagged_not_deleted(self):
        a = self.app()
        a.sources.sync("이관NAS")
        (self.nas / "메모.md").unlink()
        st = a.sources.sync("이관NAS")
        self.assertEqual(st["source_deleted"], 1)
        row = a.dms.q1("SELECT id FROM dms_docs WHERE title LIKE '%메모%'")
        self.assertEqual(a.dms.row(row["id"])["props"].get("src_deleted"), "1")

    def test_acl_rules_apply_per_path_and_dry_run_writes_nothing(self):
        a = self.app(acl_rules=[{"glob": "인사/*", "entries": [{"type": "role", "id": "hr", "role": "viewer"}], "level": 3}])
        before = a.dms.q1("SELECT COUNT(*) AS n FROM dms_docs")["n"]
        st = a.sources.sync("이관NAS", dry_run=True)
        self.assertEqual(st["migrated"], 3)
        self.assertEqual(a.dms.q1("SELECT COUNT(*) AS n FROM dms_docs")["n"], before)
        a.sources.sync("이관NAS")
        hr = a.dms.q1("SELECT id FROM dms_docs WHERE title LIKE '%평가%' AND origin='managed'")
        self.assertEqual(a.dms.row(hr["id"])["level"], 3)
        with self.assertRaises(DmsError):
            a.dms.get(a.users["kim"], hr["id"])                                                           # hidden for the department
        self.assertIn("read", a.dms.get(a.users["lee"], hr["id"])["perms"])

    def test_bad_files_are_reported_per_file_and_never_stop_the_run(self):
        (self.nas / "깨짐.docx").write_bytes(b"not a zip at all")
        (self.nas / "실행.exe").write_bytes(b"MZ")
        (self.nas / "빈.md").write_bytes(b"")
        (self.nas / "스캔.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        a = self.app()
        st = a.sources.sync("이관NAS")
        self.assertEqual(st["blocked"], 1)
        self.assertGreaterEqual(st["error"], 2)                                                           # corrupt docx and empty file
        self.assertEqual(st["migrated"], 4)                                                               # 3 text files + the scan (stored without text)
        items = {i["rel"]: i["status"] for i in a.sources.items("이관NAS")}
        self.assertEqual(items["깨짐.docx"], "error")
        self.assertEqual(items["실행.exe"], "blocked")

    def test_unknown_owner_is_refused(self):
        a = self.app(owner="nobody")
        with self.assertRaises(DmsError):
            a.sources.sync("이관NAS")


class Remote(Base):
    def run_remote(self, srv_kind):
        os.environ["T_USER"], os.environ["T_PASS"] = "u", "p"
        if srv_kind == "ftp":
            srv = fakeservers.FtpServer(self.nas)
            src = {"name": "원격", "type": "ftp", "host": "127.0.0.1", "port": srv.port, "user_env": "T_USER", "password_env": "T_PASS", "path": "/", "acl": {"depts": ["mice"], "level": 1}}
        else:
            srv = fakeservers.DavServer(self.nas)
            src = {"name": "원격", "type": "webdav", "url": srv.url, "user_env": "T_USER", "password_env": "T_PASS", "acl": {"depts": ["mice"], "level": 1}}
        self.addCleanup(srv.close)
        return make_app([src], self.tmp / "w"), srv

    def check(self, kind):
        a, srv = self.run_remote(kind)
        st = a.ingest()
        self.assertFalse(st["roots"]["원격/"]["error"], st)
        self.assertIn("원격/계약/대관계약.md", self.ids(a, "kim", "NAS대관계약본문유니크"))
        self.assertNotIn("원격/~$임시.md", {r["source"] for r in a.dms.q("SELECT source FROM dms_docs")})
        self.assertNotIn("원격/계약/대관계약.md", self.ids(a, "choi", "NAS대관계약본문유니크"))
        # incremental: only changed files are fetched again; deleted ones disappear
        cache = a.sources.local_path(a.sources.cfgs["원격"])
        before = (cache / "계약" / "대관계약.md").stat().st_mtime_ns
        (self.nas / "메모.md").unlink()
        (self.nas / "추가.md").write_text("# 추가\n\n원격추가본문유니크", "utf-8")
        a.sources.sync("원격")
        self.assertEqual((cache / "계약" / "대관계약.md").stat().st_mtime_ns, before)
        self.assertIn("원격/추가.md", self.ids(a, "kim", "원격추가본문유니크"))
        self.assertNotIn("원격/메모.md", self.ids(a, "kim", "NAS메모본문유니크"))
        # server goes away: the last good copy keeps serving and nothing is pruned
        srv.close()
        st = a.sources.sync("원격")
        self.assertIn("원격/계약/대관계약.md", self.ids(a, "kim", "NAS대관계약본문유니크"))
        self.assertFalse(a.sources.status()[0]["last"]["ok"] and a.sources.status()[0]["last"]["kind"] == "mirror")

    def test_ftp(self):
        self.check("ftp")

    def test_webdav(self):
        self.check("webdav")

    def test_wrong_password_is_an_error_not_an_empty_index(self):
        os.environ["T_USER"], os.environ["T_PASS"] = "u", "wrong"
        srv = fakeservers.DavServer(self.nas)
        self.addCleanup(srv.close)
        a = make_app([{"name": "원격", "type": "webdav", "url": srv.url, "user_env": "T_USER", "password_env": "T_PASS", "acl": {"depts": ["mice"]}}], self.tmp / "w")
        a.ingest()
        self.assertEqual(a.dms.q1("SELECT COUNT(*) AS n FROM dms_docs WHERE source LIKE '원격/%'")["n"], 0)
        last = a.sources.status()[0]["last"]
        self.assertFalse(last["ok"])
        self.assertIn("HTTPError", last["stats"]["error"])

    def test_path_traversal_names_from_the_server_are_dropped(self):
        from edm_ai.sources import _clean_segment
        for bad in ("..", ".", "a/b", "a\\b", "C:", "", "x\x00y"):
            self.assertIsNone(_clean_segment(bad))
        self.assertEqual(_clean_segment("정상 파일.md"), "정상 파일.md")


if __name__ == "__main__":
    unittest.main()


class PcAgent(test_platform.DemoCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import threading
        from http.server import ThreadingHTTPServer
        from edm_ai.server import make_handler
        os.environ["EDM_AI_SECRET"] = "t" * 32
        cls.app.cfg["auth"], cls.app.cfg["rate_limit_per_min"] = {"mode": "token"}, 5000
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app, False))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.key = cls.app.dms.create_api_key(cls.app.users["admin"], "pc", "kim", "write", 30)["key"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def test_agent_uploads_new_files_then_only_changes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("edm_sync", Path(__file__).resolve().parent.parent / "tools" / "edm_sync.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            src = Path(d)
            (src / "계약").mkdir()
            (src / "계약" / "a.md").write_text("# A\n\n에이전트첫본문유니크", "utf-8")
            (src / "b.md").write_text("# B\n\n에이전트둘째본문유니크", "utf-8")
            (src / "~$lock.md").write_text("x", "utf-8")
            (src / "run.exe").write_bytes(b"MZ")
            argv = ["--url", self.base, "--key", self.key, "--src", d, "--folder", "MICE유치팀/PC동기화시험"]
            self.assertEqual(mod.main(argv + ["--dry-run"]), 0)
            self.assertFalse((src / ".edm_sync_state.json").exists())                      # dry run writes nothing
            self.assertEqual(mod.main(argv), 0)
            state = json.loads((src / ".edm_sync_state.json").read_text("utf-8"))
            self.assertEqual(set(state), {"계약/a.md", "b.md"})
            row = self.app.dms.row(state["b.md"]["doc_id"])
            self.assertEqual((row["owner"], row["current_ver"]), ("kim", 1))                  # registered in the key's user's name
            self.assertEqual(self.app.dms.folder_path(row["folder_id"]), "MICE유치팀 / PC동기화시험")
            st = mod.sync_once(mod.EdmClient(self.base, token=self.key), src, row["folder_id"], "GEN", mod.EXCLUDE, 100, False, log=lambda *_: None)
            self.assertEqual((st["uploaded"], st["updated"], st["unchanged"]), (0, 0, 2))
            (src / "b.md").write_text("# B\n\n개정된 본문", "utf-8")
            st = mod.sync_once(mod.EdmClient(self.base, token=self.key), src, row["folder_id"], "GEN", mod.EXCLUDE, 100, False, log=lambda *_: None)
            self.assertEqual(st["updated"], 1)
            self.assertEqual(self.app.dms.row(state["b.md"]["doc_id"])["current_ver"], 2)
