import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai import backup  # noqa: E402
from edm_ai.config import App  # noqa: E402


class BackupCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.d, cls.U = cls.app.dms, cls.app.users
        cls.doc = cls.d.register(cls.U["kim"], "b.md", "# 백업\n\n백업 시험 문서 backupprobe.".encode(), doc_type="GEN", title="백업 시험")
        cls.d.new_version(cls.U["kim"], cls.doc["id"], "b.md", "# 백업\n\n두번째 버전 backupprobe.".encode())
        cls.d.add_comment(cls.U["kim"], cls.doc["id"], "백업 댓글")
        cls.out = Path(cls.tmp.name) / "bk.zip"
        cls.info = backup.backup(cls.app.store.db, cls.d.repo, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_backup_is_verifiable_and_excludes_secrets_and_external_data(self):
        ok, problems = backup.verify(self.out)
        self.assertEqual((ok, problems), (True, []))
        names = zipfile.ZipFile(self.out).namelist()
        self.assertIn("store.db", names)
        self.assertTrue([n for n in names if n.startswith("repo/") and n.endswith("b.md")])
        self.assertFalse([n for n in names if n.endswith(".json") and n != "MANIFEST.json"])    # no config / users / secrets
        self.assertFalse([n for n in names if "erp.db" in n])

    def test_restore_into_a_new_location_keeps_everything(self):
        with tempfile.TemporaryDirectory() as t:
            store, repo = Path(t) / "data" / "store.db", Path(t) / "data" / "repo"
            backup.restore(self.out, store, repo)
            cfg = json.loads((Path(self.tmp.name) / "config.json").read_text("utf-8"))
            cfg["dms"] = {**cfg.get("dms", {}), "repo": str(repo)}
            cfg["users"], cfg["docs"] = str(Path(self.tmp.name) / "users.json"), {"root": str(Path(self.tmp.name) / "docs"), "manifest": str(Path(self.tmp.name) / "docs" / "manifest.json")}
            cfg["structured"]["path"] = str(Path(self.tmp.name) / "erp.db")
            (Path(t) / "c.json").write_text(json.dumps(cfg), "utf-8")
            old = os.environ.get("EDM_AI_STORE")
            os.environ["EDM_AI_STORE"] = str(store)
            try:
                app = App(str(Path(t) / "c.json"))
            finally:
                os.environ.pop("EDM_AI_STORE") if old is None else os.environ.__setitem__("EDM_AI_STORE", old)
            d, U = app.dms, app.users
            got = d.get(U["kim"], self.doc["id"])
            self.assertEqual((got["title"], got["current_ver"]), ("백업 시험", 2))
            self.assertEqual(len(d.comments(U["kim"], self.doc["id"])), 1)
            self.assertIn(self.doc["id"], {x["id"] for x in d.search(U["kim"], "backupprobe")["items"]})
            name, data, _ = d.download(U["kim"], self.doc["id"], 1)                          # file bytes pass the SHA-256 integrity check
            self.assertIn("백업 시험 문서", data.decode())
            self.assertEqual(app.store.verify_audit(), (True, None))                          # the tamper-evident audit chain survives
            nxt = d.register(U["kim"], "n.md", "# 다음\n\n번호 이어짐.".encode(), doc_type="GEN")
            self.assertGreater(int(nxt["doc_no"][-4:]), int(self.doc["doc_no"][-4:]))         # numbering continues, no reuse

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as t:
            store, repo = Path(t) / "store.db", Path(t) / "repo"
            backup.restore(self.out, store, repo)
            with self.assertRaises(FileExistsError):
                backup.restore(self.out, store, repo)
            marker = repo / "keep.txt"
            marker.write_text("x")
            backup.restore(self.out, store, repo, force=True)
            self.assertFalse(marker.exists())                                                  # replaced as asked

    def test_tampered_missing_extra_and_unsafe_archives_are_rejected(self):
        def rebuild(mutate):
            dst = Path(self.tmp.name) / "x.zip"
            with zipfile.ZipFile(self.out) as src, zipfile.ZipFile(dst, "w") as z:
                for i in src.infolist():
                    data = src.read(i.filename)
                    r = mutate(i.filename, data)
                    if r is not None:
                        z.writestr(i.filename, r)
            return dst
        first_file = next(n for n in zipfile.ZipFile(self.out).namelist() if n.startswith("repo/"))
        altered = rebuild(lambda n, d: b"tampered" if n == first_file else d)
        ok, pr = backup.verify(altered)
        self.assertFalse(ok)
        self.assertIn("altered: " + first_file, pr)
        missing = rebuild(lambda n, d: None if n == first_file else d)
        self.assertIn("missing: " + first_file, backup.verify(missing)[1])
        extra = Path(self.tmp.name) / "e.zip"
        shutil.copy(self.out, extra)
        with zipfile.ZipFile(extra, "a") as z:
            z.writestr("repo/evil.md", "x")
        self.assertIn("not in manifest: repo/evil.md", backup.verify(extra)[1])
        for evil in ("../escape.txt", "/abs.txt", "repo/../../x.txt", "other/file.txt"):
            bad = Path(self.tmp.name) / "u.zip"
            shutil.copy(self.out, bad)
            with zipfile.ZipFile(bad, "a") as z:
                z.writestr(evil, "x")
            ok, pr = backup.verify(bad)
            self.assertFalse(ok, evil)
            with tempfile.TemporaryDirectory() as t:
                with self.assertRaises(ValueError):
                    backup.restore(bad, Path(t) / "s.db", Path(t) / "r")
                self.assertFalse((Path(t) / "s.db").exists())                                   # nothing was written
        self.assertFalse(backup.verify(Path(self.tmp.name) / "nonexistent.zip")[0])
        junk = Path(self.tmp.name) / "junk.zip"
        junk.write_bytes(b"not a zip")
        self.assertFalse(backup.verify(junk)[0])


if __name__ == "__main__":
    unittest.main()
