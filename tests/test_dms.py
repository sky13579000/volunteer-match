import base64
import io
import json
import sqlite3
import sys
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402

from edm_ai.errors import DmsError  # noqa: E402


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_platform.DemoCase.setUpClass.__func__(cls)
        cls.d = cls.app.dms
        cls.U = cls.app.users
        cls.f_sales = cls.d.q1("SELECT id FROM folders WHERE name='영업팀'")["id"]
        cls.f_fin = cls.d.q1("SELECT id FROM folders WHERE name='재무팀'")["id"]
        cls.f_hr = cls.d.q1("SELECT id FROM folders WHERE name='인사팀'")["id"]
        cls.f_rules = cls.d.q1("SELECT id FROM folders WHERE name='규정'")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    n = 0

    def reg(self, uid="kim", text="# 문서\n\n본문 내용입니다.", name="doc.md", **kw):
        Base.n += 1
        txt = f"{text} 고유{Base.n}토큰"
        return self.d.register(self.U[uid], name, txt.encode(), **kw)

    def ids(self, uid, q=None, **kw):
        return {x["id"] for x in self.d.search(self.U[uid], q, **kw)["items"]}

    def err(self, code, fn, *a, **k):
        with self.assertRaises(DmsError) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.code, code, cm.exception.msg)
        return cm.exception


class Registration(Base):
    def test_numbering_defaults_and_safe_acl(self):
        a, b = self.reg(doc_type="GEN"), self.reg(doc_type="GEN")
        self.assertRegex(a["doc_no"], r"^GEN-\d{4}-\d{4}$")
        self.assertEqual(int(b["doc_no"][-4:]), int(a["doc_no"][-4:]) + 1)
        self.assertEqual(self.d.row(a["id"])["entries"], [{"type": "dept", "id": "sales", "role": "editor"}])   # author's dept only
        self.assertIn(a["id"], self.ids("kim"))
        for other in ("jung", "park", "choi", "lee"):
            self.assertNotIn(a["id"], self.ids(other), other)

    def test_validation(self):
        p = self.U["kim"]
        self.err(400, self.d.register, p, "x.exe", b"MZ")
        self.err(400, self.d.register, p, "x.md", b"")
        self.err(400, self.d.register, p, ".hidden.md", b"x")
        self.err(400, self.d.register, p, "x.md", b"x", doc_type="NOPE")
        self.err(400, self.d.register, p, "x.md", b"x", level=3)                       # above kim's level 2
        self.err(400, self.d.register, p, "x.md", b"x", expires_at="내일")
        self.err(404, self.d.register, p, "x.md", b"x", folder_id=9999)
        self.err(409, self.d.register, p, "x.md", b"x", folder_id=2)                   # external-source folder
        self.err(422, self.d.register, p, "broken.docx", b"not a zip")
        old, self.d.max_upload = self.d.max_upload, 10
        try:
            self.err(413, self.d.register, p, "x.md", b"x" * 50)
        finally:
            self.d.max_upload = old
        self.assertFalse(list(self.d.repo.glob("*/*/broken.docx")))                    # no orphan files after a failure

    def test_filename_traversal_is_neutralised(self):
        r = self.reg(name="../../etc/evil.md")
        self.assertEqual(r["versions"][0]["filename"], "evil.md")
        self.assertTrue(all(self.d.repo.resolve() in f.resolve().parents for f in self.d.repo.rglob("evil.md")))

    def test_folder_permissions(self):
        self.err(404, self.d.register, self.U["jung"], "x.md", b"x", folder_id=self.f_sales)     # cannot even see 영업팀
        self.err(403, self.d.register, self.U["park"], "x.md", b"x", folder_id=self.f_sales)     # finance is only a viewer there
        self.err(404, self.d.register, self.U["choi"], "x.md", b"x", folder_id=self.f_hr)          # cannot even see the folder
        r = self.d.register(self.U["kim"], "ok.md", "# 영업\n\n영업팀 문서 내용입니다 폴더테스트.".encode(), folder_id=self.f_sales)
        self.assertEqual(r["folder_path"], "영업팀")
        self.assertIn(r["id"], self.ids("kim"))
        self.assertIn(r["id"], self.ids("park"))        # finance is a viewer of the folder: inherited read, published (GEN auto-approved)
        self.assertNotIn(r["id"], self.ids("jung"))

    def test_duplicate_warning_does_not_reveal_hidden_documents(self):
        body = "# 같은 파일\n\n정확히 같은 내용 중복검사용."
        a = self.d.register(self.U["kim"], "a.md", body.encode())
        again = self.d.register(self.U["kim"], "b.md", body.encode())
        self.assertEqual([x["id"] for x in again["duplicates"]], [a["id"]])
        other = self.d.register(self.U["park"], "c.md", body.encode(), folder_id=self.f_fin)
        self.assertEqual(other["duplicates"], [])                                       # park cannot read kim's copies

    def test_binary_files_are_stored_without_text(self):
        r = self.d.register(self.U["kim"], "photo.png", b"\x89PNG\r\n\x1a\nrawbytes", title="현장 사진", tags=["사진"])
        self.assertEqual(r["title"], "현장 사진")
        self.assertIn(r["id"], self.ids("kim", "현장 사진"))                              # findable by metadata
        name, data, mime = self.d.download(self.U["kim"], r["id"])
        self.assertEqual((name, mime, data[:4]), ("photo.png", "image/png", b"\x89PNG"))


class BatchConsistency(Base):
    def test_documents_created_inside_a_batch_are_immediately_usable_by_their_owner(self):
        """Regression: permission checks used the stale in-memory index while the reload was deferred."""
        with self.d.batch():
            r = self.d.register(self.U["kim"], "b.md", "# 배치\n\n배치 안에서 만든 문서 batchprobe.".encode())
            self.assertTrue(r["readable"])
            self.assertIn("edit", r["perms"])
            v = self.d.new_version(self.U["kim"], r["id"], "b.md", "# 배치\n\n두번째 batchprobe.".encode())
            self.assertEqual(v["current_ver"], 2)
            self.assertEqual(self.d.move(self.U["kim"], r["id"], self.f_sales)["folder_path"], "영업팀")
            self.assertNotIn(r["id"], self.ids("jung"))                                   # and still invisible to outsiders mid-batch
        self.assertIn(r["id"], self.ids("kim", "batchprobe"))                             # index caught up when the batch ended


class DownloadRestriction(Base):
    def test_no_download_blocks_every_export_path_but_not_reading(self):
        r = self.reg(text="# 제한\n\n다운로드 제한 시험 nodlprobe.")
        did = r["id"]
        self.d.update_meta(self.U["kim"], did, props={"no_download": "1"})
        got = self.d.get(self.U["kim"], did)
        self.assertNotIn("download", got["perms"])
        self.assertIn("read", got["perms"])
        self.assertIn("nodlprobe", got["text"])                                          # reading on screen stays possible
        self.err(403, self.d.download, self.U["kim"], did)
        data, info = self.d.export_zip(self.U["kim"], [did])
        self.assertEqual((info["exported"], info["skipped"]), (0, 1))                      # ZIP export skips it
        self.err(403, self.d.create_share, self.U["kim"], did, perm="download")            # no downloadable external link either
        self.assertTrue(self.d.create_share(self.U["kim"], did, perm="view"))              # a view-only link is still allowed
        self.d.update_meta(self.U["kim"], did, props={})
        self.assertTrue(self.d.download(self.U["kim"], did))


class Approval(Base):
    def ct(self, **kw):
        return self.reg(doc_type="CT", text="# 계약\n\nCT-2024-001 계약금액은 4억 8천만원이다.", **kw)

    def test_draft_is_private_until_approved_and_ai_follows(self):
        r = self.ct()
        self.assertEqual(r["status"], "draft")
        self.assertIsNone(r["effective_ver"])
        d = self.d
        d.submit(self.U["kim"], r["id"], ["park"])
        self.assertIn(r["id"], self.ids("park"))                                       # approver can read the draft
        self.assertNotIn(r["id"], self.ids("jung"))
        marker = f"고유{Base.n}토큰"
        ans = self.app.engine.ask(self.U["kim"], f"{marker} 계약")
        self.assertTrue([e for e in ans.evidence if e.ref.startswith("dms/" + r["doc_no"])])      # owner sees it
        ans = self.app.engine.ask(self.U["lee"], f"{marker} 계약")
        self.assertFalse([e for e in ans.evidence if r["doc_no"] in e.ref])
        out = d.approve(self.U["park"], r["id"], "확인")
        self.assertEqual((out["status"], out["effective_ver"]), ("approved", 1))
        self.assertTrue(out["retention_until"])                                        # CT: 10 years
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='kim' AND kind='approved' AND doc_id=?", (r["id"],)))

    def test_submit_validation(self):
        r = self.ct()
        for bad, code in ((["kim"], 400), (["nobody"], 400), (["park", "park"], 400), ([], 400)):
            self.err(code, self.d.submit, self.U["kim"], r["id"], bad)
        self.err(400, self.d.submit, self.U["kim"], r["id"], ["park"], mode="weird")
        self.err(404, self.d.submit, self.U["jung"], r["id"], ["park"])                  # cannot see it
        self.d.submit(self.U["kim"], r["id"], ["park"])
        self.err(409, self.d.submit, self.U["kim"], r["id"], ["lee"])                    # already in review
        self.err(409, self.d.new_version, self.U["kim"], r["id"], "v.md", b"x")           # no new versions during review

    def test_sequential_order_and_requester_cannot_approve(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park", "lee"], mode="sequential")
        self.err(403, self.d.approve, self.U["lee"], r["id"])                            # step 2 is not active yet
        self.assertNotIn(r["id"], {t["doc_id"] for t in self.d.my_tasks(self.U["lee"])})   # step 2 is not active yet
        self.d.approve(self.U["park"], r["id"])
        self.assertEqual([t["doc_id"] for t in self.d.my_tasks(self.U["lee"])], [r["id"]])
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='lee' AND kind='approval_request' AND doc_id=?", (r["id"],)))
        self.assertEqual(self.d.approve(self.U["lee"], r["id"])["status"], "approved")

    def test_parallel_needs_everyone_and_one_reject_wins(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park", "lee"], mode="parallel")
        self.assertEqual(self.d.approve(self.U["lee"], r["id"])["status"], "in_review")
        self.assertEqual(self.d.approve(self.U["park"], r["id"])["status"], "approved")
        r2 = self.ct()
        self.d.submit(self.U["kim"], r2["id"], ["park", "lee"], mode="parallel")
        self.err(400, self.d.reject, self.U["park"], r2["id"], "")                        # a reason is mandatory
        out = self.d.reject(self.U["park"], r2["id"], "금액 확인 필요")
        self.assertEqual(out["status"], "rejected")                                       # rejector gets a summary (cannot read the draft any more)
        self.assertEqual(self.d.get(self.U["kim"], r2["id"])["workflow"]["status"], "rejected")
        self.err(404, self.d.approve, self.U["lee"], r2["id"])         # nothing pending: not an approver any more

    def test_resubmit_after_rejection_and_cancel(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park"])
        self.d.reject(self.U["park"], r["id"], "수정 요청")
        self.d.new_version(self.U["kim"], r["id"], "doc.md", b"# fixed\n\nCT-2024-001 revised.")
        self.assertEqual(self.d.submit(self.U["kim"], r["id"], ["park"])["workflow"]["status"], "pending")
        self.err(403, self.d.cancel_workflow, self.U["park"], r["id"])                   # only requester / admin
        out = self.d.cancel_workflow(self.U["kim"], r["id"])
        self.assertEqual(out["status"], "draft")
        self.assertNotIn(r["id"], {t["doc_id"] for t in self.d.my_tasks(self.U["park"])})   # cancelled: no longer in park's queue

    def test_delegation(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park"])
        self.err(400, self.d.delegate, self.U["park"], r["id"], "kim")                   # requester / owner excluded
        self.d.delegate(self.U["park"], r["id"], "lee")
        self.assertNotIn(r["id"], {t["doc_id"] for t in self.d.my_tasks(self.U["park"])})
        self.err(404, self.d.approve, self.U["park"], r["id"])
        self.assertIn(r["id"], self.ids("lee"))                                          # delegate can now read the draft
        self.assertEqual(self.d.approve(self.U["lee"], r["id"])["status"], "approved")

    def test_non_approver_cannot_act_and_learns_nothing(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park"])
        for fn in (self.d.approve, self.d.reject, ):
            self.err(404, fn, self.U["jung"], r["id"], "x")

    def test_new_version_of_published_doc_keeps_old_one_live_until_approved(self):
        r = self.ct()
        self.d.submit(self.U["kim"], r["id"], ["park"])
        self.d.approve(self.U["park"], r["id"])
        v2 = self.d.new_version(self.U["kim"], r["id"], "doc.md", "# 계약\n\n개정안 초안 개정토큰.".encode(), "개정")
        self.assertEqual((v2["status"], v2["effective_ver"], v2["current_ver"]), ("draft", 1, 2))
        # editors see the draft v2; the effective v1 stays the published one
        self.assertEqual(self.d.get(self.U["kim"], r["id"])["viewing_ver"], 2)
        self.assertEqual([v["ver"] for v in self.d.versions(self.U["kim"], r["id"])], [2, 1])


class Versions(Base):
    def test_versions_diff_restore_and_checkout(self):
        r = self.reg(text="# 규정\n\n제1조 목적.\n\n제2조 적용 범위.")
        did = r["id"]
        v2 = self.d.new_version(self.U["kim"], did, "doc.md", "# 규정\n\n제1조 목적.\n\n제2조 적용 범위를 확대한다.\n\n제3조 신설.".encode(), "개정")
        self.assertEqual((v2["current_ver"], v2["effective_ver"]), (2, 2))               # GEN: auto-approved
        diff = self.d.diff(self.U["kim"], did, 1, 2)
        self.assertGreater(diff["added"], 0)
        self.assertTrue([l for l in diff["lines"] if l["tag"] == "add" and "제3조" in l["text"]])
        self.assertTrue(self.d.diff(self.U["kim"], did, 1, 1)["same"])
        self.err(409, self.d.new_version, self.U["kim"], did, "doc.md", "# 규정\n\n제1조 목적.\n\n제2조 적용 범위를 확대한다.\n\n제3조 신설.".encode())   # identical to the latest version
        v3 = self.d.restore(self.U["kim"], did, 1)
        self.assertEqual(v3["current_ver"], 3)
        self.assertEqual(self.d.diff(self.U["kim"], did, 1, 3)["same"], True)
        self.assertEqual([v["status"] for v in self.d.versions(self.U["kim"], did)], ["approved", "superseded", "superseded"])
        self.err(404, self.d.diff, self.U["kim"], did, 1, 99)

    def test_checkout_blocks_others_and_checkin_releases(self):
        r = self.d.register(self.U["kim"], "c.md", "# 협업\n\n체크아웃 시험 문서입니다.".encode(), folder_id=self.f_sales)
        did = r["id"]
        self.d.checkout(self.U["kim"], did)
        self.assertEqual(self.d.get(self.U["kim"], did)["locked_by"], "kim")
        # another editor of the folder (a sales colleague is not in the demo users, so grant jung editor via entries)
        self.d.set_acl(self.U["kim"], did, entries=[{"type": "dept", "id": "sales", "role": "editor"}, {"type": "user", "id": "jung", "role": "editor"}])
        self.err(409, self.d.checkout, self.U["jung"], did)
        self.err(409, self.d.new_version, self.U["jung"], did, "c.md", b"# jung change")
        self.err(403, self.d.cancel_checkout, self.U["jung"], did)
        self.d.new_version(self.U["kim"], did, "c.md", "# 협업\n\n수정한 내용입니다 체크인.".encode())
        self.assertIsNone(self.d.get(self.U["kim"], did)["locked_by"])                   # checking in releases the lock
        self.d.checkout(self.U["jung"], did)
        self.assertEqual(self.d.cancel_checkout(self.U["admin"], did)["locked_by"], None)   # admin can force-release

    def test_integrity_check_on_download(self):
        r = self.reg(text="# 무결성\n\n파일이 바뀌면 안 됩니다.")
        v = self.d._ver(self.d.row(r["id"]), 1)
        (self.d.repo / v["path"]).write_bytes(b"tampered on disk")
        self.err(500, self.d.download, self.U["kim"], r["id"])


class Permissions(Base):
    def test_roles_viewer_editor_manager(self):
        r = self.reg(text="# 권한\n\n역할 시험 문서입니다.")
        did = r["id"]
        self.d.set_acl(self.U["kim"], did, entries=[{"type": "user", "id": "park", "role": "viewer"}, {"type": "user", "id": "jung", "role": "editor"},
                                                    {"type": "user", "id": "lee", "role": "manager"}])
        pv, pe, pm = (set(self.d.get(self.U[u], did)["perms"]) for u in ("park", "jung", "lee"))
        self.assertTrue({"read", "download", "comment"} <= pv and not ({"edit", "delete", "acl"} & pv))
        self.assertTrue("edit" in pe and not ({"delete", "acl", "share"} & pe))
        self.assertTrue({"edit", "delete", "acl", "share"} <= pm)
        self.err(403, self.d.new_version, self.U["park"], did, "x.md", b"x")
        self.err(403, self.d.update_meta, self.U["park"], did, title="변조")
        self.err(403, self.d.delete, self.U["jung"], did)
        self.err(403, self.d.set_acl, self.U["jung"], did, entries=[])
        self.err(404, self.d.get, self.U["choi"], did)

    def test_hidden_is_404_for_every_operation(self):
        r = self.reg(text="# 비밀\n\n숨겨진 문서입니다.")
        did, p = r["id"], self.U["choi"]
        for fn, args in ((self.d.get, ()), (self.d.versions, ()), (self.d.download, ()), (self.d.comments, ()), (self.d.checkout, ()),
                         (self.d.delete, ()), (self.d.submit, (["park"],)), (self.d.set_favorite, (True,)), (self.d.diff, (1, 1))):
            self.err(404, fn, p, did, *args)

    def test_admin_manages_but_cannot_read(self):
        r = self.reg(text="# 관리\n\n관리자가 읽으면 안 되는 문서.")
        a = self.U["admin"]
        got = self.d.get(a, r["id"])
        self.assertNotIn("text", got)
        self.assertFalse(got["readable"])
        self.assertIn(r["id"], self.ids("admin"))                                        # listed (flagged) when not searching
        self.assertNotIn(r["id"], self.ids("admin", "관리자가 읽으면"))                    # but never findable by content
        self.err(403, self.d.download, a, r["id"])                                       # administering is not reading
        self.assertTrue(self.d.set_acl(a, r["id"], entries=[{"type": "dept", "id": "sales", "role": "viewer"}]))
        self.assertTrue(self.d.delete(a, r["id"]))

    def test_folder_inheritance_and_cascade(self):
        top = self.d.create_folder(self.U["admin"], None, "상속시험")
        self.d.set_folder_acl(self.U["admin"], top["id"], [{"type": "dept", "id": "sales", "role": "editor"}])
        sub = self.d.create_folder(self.U["admin"], top["id"], "하위")
        r = self.d.register(self.U["kim"], "i.md", "# 상속\n\n상속 시험 문서입니다 cascade.".encode(), folder_id=sub["id"])
        self.assertEqual(self.d.row(r["id"])["entries"], [])                              # nothing at document level
        self.assertNotIn(r["id"], self.ids("park"))
        self.d.set_folder_acl(self.U["admin"], top["id"], [{"type": "dept", "id": "sales", "role": "editor"}, {"type": "dept", "id": "finance", "role": "viewer"}])
        self.assertIn(r["id"], self.ids("park"))                                          # the change cascaded into the index
        self.d.set_acl(self.U["kim"], r["id"], inherit=False, entries=[{"type": "dept", "id": "sales", "role": "editor"}])
        self.assertNotIn(r["id"], self.ids("park"))                                       # inheritance switched off on the document

    def test_move_changes_who_can_read(self):
        r = self.d.register(self.U["kim"], "m.md", "# 이동\n\n이동 시험 문서입니다 moveprobe.".encode(), folder_id=self.f_sales)
        self.assertIn(r["id"], self.ids("park"))
        moved = self.d.move(self.U["kim"], r["id"], 1)                                    # open_create folder, no entries
        self.assertEqual(moved["folder_path"], "미분류")

    def test_deny_entry_overrides_grants(self):
        r = self.reg(text="# 제외\n\n제외 시험 문서입니다.")
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "dept", "id": "sales", "role": "viewer"}, {"type": "dept", "id": "finance", "role": "viewer"},
                                                        {"type": "deny", "id": "park"}])
        self.assertNotIn(r["id"], self.ids("park"))

    def test_entry_validation(self):
        r = self.reg(text="# 검증\n\n입력 검증 문서입니다.")
        for bad in ([{"type": "group", "id": "x", "role": "viewer"}], [{"type": "user", "id": "", "role": "viewer"}], [{"type": "user", "id": "x", "role": "god"}], "str", [1]):
            self.err(400, self.d.set_acl, self.U["kim"], r["id"], entries=bad)
        self.err(400, self.d.set_acl, self.U["kim"], r["id"], level=3)                    # above the owner's clearance
        self.assertTrue(self.d.set_acl(self.U["admin"], r["id"], level=5))


class Lifecycle(Base):
    def past(self, did, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        self.d.x(f"UPDATE dms_docs SET {sets} WHERE id=?", (*cols.values(), did))

    def test_retention_and_legal_hold_block_deletion_even_for_admin(self):
        r = self.reg(doc_type="MN", text="# 회의\n\n보존 시험 회의록.")                        # MN: 3-year retention
        self.assertTrue(r["retention_until"])
        self.err(409, self.d.delete, self.U["kim"], r["id"])
        self.err(409, self.d.delete, self.U["admin"], r["id"])
        self.past(r["id"], retention_until="2001-01-01")
        self.err(403, self.d.set_hold, self.U["kim"], r["id"], True)
        self.d.set_hold(self.U["admin"], r["id"], True, "소송")
        self.err(409, self.d.delete, self.U["kim"], r["id"])
        self.d.set_hold(self.U["admin"], r["id"], False)
        self.assertEqual(self.d.delete(self.U["kim"], r["id"])["restorable_days"], 30)

    def test_trash_restore_purge(self):
        r = self.reg(text="# 휴지통\n\n휴지통 시험 문서 trashprobe.")
        did = r["id"]
        self.d.delete(self.U["kim"], did)
        self.assertNotIn(did, self.ids("kim"))                                            # out of search immediately
        self.assertFalse([e for e in self.app.engine.ask(self.U["kim"], "휴지통 시험 문서 trashprobe").evidence if r["doc_no"] in e.ref])
        self.assertIn(did, {t["id"] for t in self.d.trash(self.U["kim"])})
        self.assertNotIn(did, {t["id"] for t in self.d.trash(self.U["jung"])})
        self.err(404, self.d.restore_doc, self.U["jung"], did)
        self.assertTrue(self.d.restore_doc(self.U["kim"], did))
        self.assertIn(did, self.ids("kim"))
        self.d.delete(self.U["kim"], did)
        self.err(403, self.d.purge, self.U["kim"], did)
        path = self.d.repo / str(did)
        self.assertTrue(path.exists())
        self.d.purge(self.U["admin"], did)
        self.assertFalse(path.exists())
        self.assertIsNone(self.d.row(did))

    def test_maintenance_expires_warns_and_purges(self):
        soon = self.reg(text="# 곧 만료\n\n만료 예정 문서 expiryprobe1.", expires_at=(__import__("datetime").date.today() + __import__("datetime").timedelta(days=10)).isoformat())
        gone = self.reg(text="# 만료\n\n이미 만료된 문서 expiryprobe2.")
        self.past(gone["id"], expires_at="2020-01-01")
        old = self.reg(text="# 오래된 휴지통\n\n자동 정리 대상 expiryprobe3.")
        self.d.delete(self.U["kim"], old["id"])
        self.past(old["id"], deleted_at="2020-01-01T00:00:00")
        self.err(403, self.d.maintenance, self.U["kim"])
        res = self.d.maintenance(self.U["admin"])
        self.assertTrue(res["expired"] >= 1 and res["warned"] >= 1 and res["purged"] >= 1, res)
        self.assertEqual(self.d.row(gone["id"])["status"], "expired")
        self.assertNotIn(gone["id"], self.ids("kim", "expiryprobe2"))                      # expired = out of search / AI
        self.assertIsNone(self.d.row(old["id"]))
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='kim' AND kind='expiring' AND doc_id=?", (soon["id"],)))
        self.assertEqual(self.d.maintenance(self.U["admin"])["warned"], 0)                  # warned only once
        self.d.extend_expiry(self.U["kim"], gone["id"], "2099-01-01")
        self.assertIn(gone["id"], self.ids("kim", "expiryprobe2"))                          # extended -> back in service

    def test_legal_hold_prevents_expiry_and_purge(self):
        r = self.reg(text="# 보류\n\n보류 문서 holdprobe.")
        self.past(r["id"], expires_at="2020-01-01")
        self.d.set_hold(self.U["admin"], r["id"], True)
        self.d.maintenance(self.U["admin"])
        self.assertEqual(self.d.row(r["id"])["status"], "approved")


class Collaboration(Base):
    def test_comments_mentions_and_notifications(self):
        r = self.reg(text="# 협업\n\n댓글 시험 문서입니다.")
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "dept", "id": "sales", "role": "editor"}, {"type": "user", "id": "park", "role": "viewer"}])
        c = self.d.add_comment(self.U["kim"], r["id"], "@park 확인 부탁드립니다 @choi 도 봐주세요")
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='park' AND kind='mention' AND doc_id=?", (r["id"],)))
        self.assertIsNone(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='choi' AND doc_id=?", (r["id"],)))   # a mention never reveals a document
        self.d.add_comment(self.U["park"], r["id"], "확인했습니다", parent_id=c["id"])
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM notifications WHERE user_id='kim' AND kind='comment' AND doc_id=?", (r["id"],)))
        self.assertEqual(len(self.d.comments(self.U["kim"], r["id"])), 2)
        self.err(400, self.d.add_comment, self.U["kim"], r["id"], "  ")
        self.err(404, self.d.add_comment, self.U["kim"], r["id"], "답글", parent_id=9999)
        self.err(404, self.d.add_comment, self.U["choi"], r["id"], "침입")
        self.err(403, self.d.delete_comment, self.U["park"], r["id"], c["id"])                    # not their comment
        self.d.delete_comment(self.U["kim"], r["id"], c["id"])
        self.assertEqual(len(self.d.comments(self.U["kim"], r["id"])), 1)

    def test_notification_text_hidden_after_access_is_lost(self):
        r = self.reg(text="# 알림\n\n알림 시험 문서입니다.")
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "user", "id": "park", "role": "viewer"}])
        self.d.add_comment(self.U["kim"], r["id"], "@park 비밀 내용 secretnote")
        self.assertIn("secretnote", json.dumps(self.d.notifications(self.U["park"]), ensure_ascii=False))
        self.d.set_acl(self.U["kim"], r["id"], entries=[])
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "dept", "id": "sales", "role": "editor"}])
        txt = json.dumps(self.d.notifications(self.U["park"]), ensure_ascii=False)
        self.assertNotIn("secretnote", txt)

    def test_mark_read_only_touches_own(self):
        self.d.notify("park", "x", None, "p1")
        self.d.notify("kim", "x", None, "k1")
        before = self.d.notifications(self.U["kim"])["unread"]
        self.d.mark_read(self.U["park"])
        self.assertEqual(self.d.notifications(self.U["park"])["unread"], 0)
        self.assertEqual(self.d.notifications(self.U["kim"])["unread"], before)

    def test_favorites_recent_saved_searches(self):
        r = self.reg(text="# 즐겨찾기\n\n즐겨찾기 시험.")
        self.d.set_favorite(self.U["kim"], r["id"], True)
        self.assertIn(r["id"], {x["id"] for x in self.d.favorites(self.U["kim"])})
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "user", "id": "park", "role": "viewer"}])
        self.d.set_favorite(self.U["park"], r["id"], True)
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "dept", "id": "sales", "role": "editor"}])
        self.assertNotIn(r["id"], {x["id"] for x in self.d.favorites(self.U["park"])})           # a stale favourite reveals nothing
        self.d.get(self.U["kim"], r["id"])
        self.assertIn(r["id"], {x["id"] for x in self.d.recent(self.U["kim"])})
        s = self.d.save_search(self.U["kim"], "내 계약", {"q": "계약", "type": "CT"})
        self.assertEqual(self.d.saved_searches(self.U["kim"])[0]["query"]["type"], "CT")
        self.assertEqual(self.d.saved_searches(self.U["park"]), [])
        self.err(404, self.d.delete_saved_search, self.U["park"], s["id"])
        self.d.delete_saved_search(self.U["kim"], s["id"])


class RelatedDocs(Base):
    def test_links_are_symmetric_permission_aware_and_leak_nothing(self):
        a, b = self.reg(text="# 가\n\n관련 가."), self.reg(text="# 나\n\n관련 나.")
        hidden = self.d.register(self.U["park"], "h.md", "# 재무\n\n숨김 문서.".encode(), folder_id=self.f_fin)
        self.d.link_docs(self.U["kim"], a["id"], b["doc_no"])                              # by document number
        self.assertEqual([x["id"] for x in self.d.get(self.U["kim"], a["id"])["related"]], [b["id"]])
        self.assertEqual([x["id"] for x in self.d.get(self.U["kim"], b["id"])["related"]], [a["id"]])   # shows on the other side too
        self.err(404, self.d.link_docs, self.U["kim"], a["id"], hidden["doc_no"])           # cannot link what you cannot read
        self.err(404, self.d.link_docs, self.U["kim"], a["id"], "NOPE-1")
        self.err(400, self.d.link_docs, self.U["kim"], a["id"], a["id"])
        self.err(404, self.d.link_docs, self.U["jung"], a["id"], b["id"])                   # jung cannot see the source document
        # b's owner can later grant a colleague access to b only; that person must not see the link to a
        self.d.set_acl(self.U["kim"], a["id"], entries=[{"type": "user", "id": "park", "role": "viewer"}])
        self.assertEqual(self.d.get(self.U["park"], a["id"])["related"], [])               # park reads a but not b: b is not revealed
        self.d.unlink_docs(self.U["kim"], a["id"], b["id"])
        self.assertEqual(self.d.get(self.U["kim"], b["id"])["related"], [])
        self.err(404, self.d.unlink_docs, self.U["kim"], a["id"], b["id"])
        self.d.link_docs(self.U["kim"], a["id"], b["id"])
        self.d.delete(self.U["kim"], b["id"])
        self.assertEqual(self.d.get(self.U["kim"], a["id"])["related"], [])               # trashed documents drop out of the list


class SearchAndDashboard(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = cls.d.register(cls.U["kim"], "a.md", "# 검색 갑\n\n검색패싯 갑 문서 내용 alphaterm.".encode(), doc_type="GEN", tags=["갑", "공통"], title="검색 갑")
        cls.b = cls.d.register(cls.U["kim"], "b.md", "# 검색 을\n\n검색패싯 을 문서 내용 alphaterm betaonly.".encode(), doc_type="MN", tags=["을", "공통"], title="검색 을")

    def test_filters_facets_pagination_and_sort(self):
        s = self.d.search(self.U["kim"], "alphaterm")
        self.assertEqual({x["id"] for x in s["items"]}, {self.a["id"], self.b["id"]})
        self.assertEqual({f["value"] for f in s["facets"]["type"]}, {"GEN", "MN"})
        self.assertEqual(self.ids("kim", "alphaterm", type="MN"), {self.b["id"]})
        self.assertEqual(self.ids("kim", "alphaterm", tag="갑"), {self.a["id"]})
        self.assertEqual(self.ids("kim", "alphaterm", owner="kim"), {self.a["id"], self.b["id"]})
        self.assertEqual(self.ids("kim", "alphaterm", owner="park"), set())
        self.assertEqual(self.ids("kim", "alphaterm", date_from="2999-01-01"), set())
        p1 = self.d.search(self.U["kim"], "alphaterm", size=1, page=1, sort="title")
        p2 = self.d.search(self.U["kim"], "alphaterm", size=1, page=2, sort="title")
        self.assertEqual((p1["total"], len(p1["items"]), len(p2["items"])), (2, 1, 1))
        self.assertNotEqual(p1["items"][0]["id"], p2["items"][0]["id"])
        self.assertEqual([x["title"] for x in self.d.search(self.U["kim"], "alphaterm", sort="title")["items"]], ["검색 갑", "검색 을"])
        self.err(400, self.d.search, self.U["kim"], "x", date_from="어제")

    def test_snippet_has_highlight_offsets(self):
        it = self.d.search(self.U["kim"], "betaonly")["items"][0]
        sn = it["snippet"]
        self.assertTrue(sn["hits"])
        a, b = sn["hits"][0]
        self.assertEqual(sn["text"][a:b].lower(), "betaonly")

    def test_metadata_only_hit_by_title_doc_no_and_tag(self):
        self.assertIn(self.a["id"], self.ids("kim", self.a["doc_no"]))
        self.assertIn(self.b["id"], self.ids("kim", "을"))

    def test_search_never_returns_unreadable_documents(self):
        for u in ("jung", "park", "choi", "lee"):
            self.assertFalse({self.a["id"], self.b["id"]} & self.ids(u, "alphaterm"), u)
            self.assertFalse({self.a["id"], self.b["id"]} & self.ids(u), u)
        self.assertFalse({x["value"] for x in self.d.search(self.U["choi"], "alphaterm")["facets"]["owner"]})   # facets leak nothing either

    def test_dashboard(self):
        db = self.d.dashboard(self.U["kim"])
        self.assertGreaterEqual(db["counts"]["mine"], 2)
        self.assertIn("GEN", {t["code"] for t in db["by_type"]})
        self.assertEqual(db["storage_scope"], "mine")
        self.assertEqual(self.d.dashboard(self.U["admin"])["storage_scope"], "all")
        self.assertEqual(self.d.dashboard(self.U["choi"])["counts"]["mine"], 0)


class Sharing(Base):
    def pub(self, **kw):
        r = self.reg(doc_type="GEN", text="# 공유\n\n외부 공유 시험. 연락처 010-1234-5678 sharebody.", **kw)
        return r

    def test_create_requires_published_manager_and_low_level(self):
        r = self.pub()
        self.err(404, self.d.create_share, self.U["choi"], r["id"])
        self.d.set_acl(self.U["kim"], r["id"], entries=[{"type": "dept", "id": "sales", "role": "editor"}, {"type": "user", "id": "park", "role": "viewer"}])
        self.err(403, self.d.create_share, self.U["park"], r["id"])                       # viewers cannot share
        ct = self.reg(doc_type="CT", text="# 계약\n\n초안은 공유 불가.")
        self.err(409, self.d.create_share, self.U["kim"], ct["id"])                      # unpublished draft
        hi = self.d.set_acl(self.U["admin"], self.pub()["id"], level=2)
        self.err(403, self.d.create_share, self.U["kim"], hi["id"])                       # above max_level
        for kw in ({"days": 0}, {"days": 365}, {"max_downloads": -1}, {"perm": "edit"}, {"password": "123"}):
            self.err(400, self.d.create_share, self.U["kim"], r["id"], **kw)

    def test_token_is_stored_hashed_and_works_without_login(self):
        r = self.pub()
        s = self.d.create_share(self.U["kim"], r["id"], days=3)
        stored = self.d.q1("SELECT token_hash FROM shares WHERE id=?", (s["id"],))["token_hash"]
        self.assertNotIn(s["token"], json.dumps(self.d.q("SELECT * FROM shares")))
        self.assertEqual(len(stored), 64)
        info = self.d.share_info(s["token"])
        self.assertEqual(info["title"], r["title"])
        self.assertIn("[전화번호]", info["text"])                                          # external viewers always get PII redacted
        self.assertNotIn("1234", info["text"])
        self.assertTrue(self.d.q1("SELECT 1 AS x FROM audit WHERE question LIKE '[외부 공유 열람]%'"))
        self.assertEqual(self.d.list_shares(self.U["kim"], r["id"])[0]["views"], 1)

    def test_every_failure_looks_the_same(self):
        r = self.pub()
        s = self.d.create_share(self.U["kim"], r["id"])
        msgs = set()
        for token in ("nonsense", s["token"] + "x", ""):
            msgs.add(self.err(404, self.d.share_info, token).msg)
        self.d.revoke_share(self.U["kim"], r["id"], s["id"])
        msgs.add(self.err(404, self.d.share_info, s["token"]).msg)
        s2 = self.d.create_share(self.U["kim"], r["id"])
        self.d.x("UPDATE shares SET expires='2000-01-01T00:00:00' WHERE id=?", (s2["id"],))
        msgs.add(self.err(404, self.d.share_info, s2["token"]).msg)
        self.assertEqual(len(msgs), 1)                                                    # revoked / expired / unknown are indistinguishable

    def test_password_lockout_and_download_limits(self):
        r = self.pub()
        s = self.d.create_share(self.U["kim"], r["id"], password="correct-horse", perm="download", max_downloads=2)
        self.assertEqual(self.d.share_info(s["token"]), {"requires_password": True})       # nothing about the document before the password
        self.err(403, self.d.share_access, s["token"], "wrong")
        self.assertEqual(self.d.share_access(s["token"], "correct-horse")["title"], r["title"])
        for _ in range(5):
            self.err(403, self.d.share_access, s["token"], "bad")
        self.err(429, self.d.share_access, s["token"], "correct-horse")                    # locked out even with the right password
        self.d.x("UPDATE shares SET locked_until=0 WHERE id=?", (s["id"],))
        name, data, _ = self.d.share_download(s["token"], "correct-horse")
        self.assertEqual(name, "doc.md")
        self.d.share_download(s["token"], "correct-horse")
        self.err(403, self.d.share_download, s["token"], "correct-horse")                  # download budget used up

    def test_view_only_link_cannot_download(self):
        r = self.pub()
        s = self.d.create_share(self.U["kim"], r["id"], perm="view")
        self.err(403, self.d.share_download, s["token"])

    def test_link_dies_when_document_is_superseded_deleted_or_expired(self):
        r = self.pub()
        s = self.d.create_share(self.U["kim"], r["id"])
        self.d.new_version(self.U["kim"], r["id"], "doc.md", "# 공유\n\n개정본 supersede.".encode())
        self.err(404, self.d.share_info, s["token"])
        s2 = self.d.create_share(self.U["kim"], r["id"])
        self.d.x("UPDATE dms_docs SET status='expired' WHERE id=?", (r["id"],))
        self.err(404, self.d.share_info, s2["token"])


class BulkAndZip(Base):
    def test_bulk_partial_failure_and_no_existence_leak(self):
        a, b = self.reg(), self.reg()
        other = self.d.register(self.U["park"], "p.md", "# 재무\n\n재무 문서.".encode(), folder_id=self.f_fin)
        res = self.d.bulk(self.U["kim"], "add_tags", [a["id"], b["id"], other["id"], 99999], {"tags": ["일괄"]})
        self.assertEqual((res["ok"], res["failed"]), (2, 2))
        msgs = {r["id"]: r.get("error") for r in res["results"] if not r["ok"]}
        self.assertEqual(msgs[other["id"]], msgs[99999])                                   # hidden and missing look identical
        self.assertIn("일괄", self.d.get(self.U["kim"], a["id"])["tags"])
        self.d.bulk(self.U["kim"], "remove_tags", [a["id"]], {"tags": ["일괄"]})
        self.assertNotIn("일괄", self.d.get(self.U["kim"], a["id"])["tags"])
        self.assertEqual(self.d.bulk(self.U["kim"], "move", [a["id"], b["id"]], {"folder_id": self.f_sales})["ok"], 2)
        self.err(400, self.d.bulk, self.U["kim"], "explode", [a["id"]])
        self.err(400, self.d.bulk, self.U["kim"], "delete", [])
        self.err(400, self.d.bulk, self.U["kim"], "delete", list(range(500)))

    def test_export_zip_respects_permissions(self):
        a = self.reg(text="# 내보내기\n\n내보낼 문서.", title="=SUM(1+1) 제목")
        hidden = self.d.register(self.U["park"], "h.md", "# 재무\n\n숨김.".encode(), folder_id=self.f_fin)
        data, info = self.d.export_zip(self.U["kim"], [a["id"], hidden["id"], 99999])
        self.assertEqual((info["exported"], info["skipped"]), (1, 2))
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        self.assertTrue(any(n.endswith("doc.md") for n in names))
        self.assertNotIn(hidden["doc_no"], " ".join(names))
        manifest = z.read("_manifest.csv").decode("utf-8-sig")
        self.assertIn("'=SUM(1+1) 제목", manifest)                                         # CSV formula injection neutralised
        self.assertNotIn(hidden["doc_no"], z.read("_skipped.txt").decode())

    def _zip(self, files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for k, v in files.items():
                z.writestr(k, v)
        return b.getvalue()

    def test_zip_import_builds_folders_and_skips_bad_entries(self):
        data = self._zip({"계약/2026/a.md": "# 가\n\n일괄 등록 가 zipprobe.", "계약/b.txt": "일괄 등록 나 zipprobe", "x.exe": "MZ",
                          "../escape.md": "# 탈출", "__MACOSX/._a.md": "junk", ".DS_Store": "junk", "c.docx": "not really a docx", "빈/": ""})
        res = self.d.import_zip(self.U["kim"], self.f_sales, data, doc_type="GEN")
        self.assertEqual(len(res["created"]), 2)
        reasons = {s["path"]: s["reason"] for s in res["skipped"]}
        self.assertIn("../escape.md", reasons)
        self.assertIn("x.exe", reasons)
        self.assertIn("c.docx", reasons)
        self.assertNotIn(".DS_Store", reasons)
        paths = {self.d.get(self.U["kim"], c["id"])["folder_path"] for c in res["created"]}
        self.assertEqual(paths, {"영업팀 / 계약 / 2026", "영업팀 / 계약"})
        self.assertFalse((self.d.repo.parent / "escape.md").exists())

    def test_zip_import_into_the_open_registration_folder(self):
        """Regression: single-file registration allowed the open folder but ZIP import demanded an explicit role."""
        res = self.d.import_zip(self.U["choi"], 1, self._zip({"x/a.md": "# 열린 폴더\n\nzipopenprobe"}))
        self.assertEqual(len(res["created"]), 1)
        self.err(403, self.d.import_zip, self.U["choi"], self.f_sales, self._zip({"a.md": "x"}))

    def test_zip_import_limits_and_permissions(self):
        self.err(403, self.d.import_zip, self.U["jung"], self.f_sales, self._zip({"a.md": "x"}))
        self.err(422, self.d.import_zip, self.U["kim"], self.f_sales, b"not a zip")
        self.err(413, self.d.import_zip, self.U["kim"], self.f_sales, self._zip({f"f{i}.md": "x" for i in range(501)}))
        old, self.d.max_upload = self.d.max_upload, 100
        try:
            res = self.d.import_zip(self.U["kim"], self.f_sales, self._zip({"big.md": "x" * 5000, "ok.md": "# 작은 파일\n\nzipsmall"}))
            self.assertEqual((len(res["created"]), len(res["skipped"])), (1, 1))
        finally:
            self.d.max_upload = old


class Admin(Base):
    def test_doc_types(self):
        self.err(403, self.d.save_doc_type, self.U["kim"], {"code": "ZZ", "name": "x"})
        for bad in ({"code": "z", "name": "x"}, {"code": "EXT", "name": "x"}, {"code": "ZZ", "name": ""}, {"code": "ZZ", "name": "x", "pattern": "{evil.__class__}"},
                    {"code": "ZZ", "name": "x", "pattern": "{seq!r:99999999}" }, {"code": "ZZ", "name": "x", "retention_years": 500},
                    {"code": "ZZ", "name": "x", "default_approvers": ["ghost"]}):
            self.err(400, self.d.save_doc_type, self.U["admin"], bad)
        t = self.d.save_doc_type(self.U["admin"], {"code": "PO", "name": "발주서", "pattern": "PO-{year}-{seq:05}", "retention_years": 5,
                                                  "approval_required": True, "default_approvers": ["park"]})
        self.assertEqual(t["pattern"], "PO-{year}-{seq:05}")
        r = self.reg(doc_type="PO")
        self.assertRegex(r["doc_no"], r"^PO-\d{4}-\d{5}$")
        self.assertEqual(self.d.submit(self.U["kim"], r["id"])["workflow"]["steps"][0]["approver"], "park")     # default approvers
        self.d.save_doc_type(self.U["admin"], {"code": "PO", "name": "발주서", "active": False})
        self.err(400, self.d.register, self.U["kim"], "x.md", b"x", doc_type="PO")
        self.assertNotIn("PO", {x["code"] for x in self.d.list_doc_types(self.U["kim"])})

    def test_audit_search_csv_and_stats(self):
        r = self.reg(text="# 감사\n\n감사 시험.", title="@evil 제목")
        self.err(403, self.d.audit_search, self.U["kim"])
        res = self.d.audit_search(self.U["admin"], user="kim", action="registered")
        self.assertTrue(res["chain_ok"])
        self.assertTrue([e for e in res["entries"] if r["doc_no"] in e["what"]])
        self.assertFalse(self.d.audit_search(self.U["admin"], user="nobody")["entries"])
        csv_text = self.d.audit_csv(self.U["admin"], user="kim")
        self.assertTrue(csv_text.startswith("﻿"))
        self.assertFalse([l for l in csv_text.splitlines()[1:] if l[:1] in "=+-@"])
        st = self.d.admin_stats(self.U["admin"])
        self.assertGreater(st["documents"], 0)
        self.err(403, self.d.admin_stats, self.U["kim"])


class CrawledDocs(Base):
    def test_crawled_documents_are_listed_but_read_only(self):
        listed = {x["source"]: x for x in self.d.search(self.U["park"], origin="crawled")["items"]}
        self.assertIn("contracts/한빛전자_MES_계약요약.md", listed)
        doc = listed["contracts/한빛전자_MES_계약요약.md"]
        self.assertTrue(doc["doc_no"].startswith("EXT-"))
        full = self.d.get(self.U["park"], doc["id"])
        self.assertNotIn("edit", full["perms"])
        self.err(403, self.d.new_version, self.U["park"], doc["id"], "x.md", b"x")
        self.assertEqual(self.d.add_comment(self.U["park"], doc["id"], "외부 문서 댓글")["text"], "외부 문서 댓글")
        name, data, _ = self.d.download(self.U["park"], doc["id"])
        self.assertEqual(name, "한빛전자_MES_계약요약.md")
        self.assertIn("지체상금", data.decode())
        hr = next(x for x in self.d.search(self.U["lee"], origin="crawled")["items"] if x["source"].startswith("hr/"))
        self.err(404, self.d.get, self.U["kim"], hr["id"])
        self.err(404, self.d.download, self.U["kim"], hr["id"])

    def test_admin_changes_crawled_acl_and_it_survives_reingest(self):
        d = next(x for x in self.d.search(self.U["choi"], origin="crawled")["items"] if x["source"].startswith("drawings/"))
        self.d.set_acl(self.U["admin"], d["id"], entries=[{"type": "role", "id": "hr", "role": "viewer"}], level=3)
        self.app.ingest()
        self.assertNotIn(d["id"], self.ids("choi"))
        self.d.set_acl(self.U["admin"], d["id"], entries=[], level=1)
        self.assertIn(d["id"], self.ids("choi"))
        self.err(403, self.d.set_acl, self.U["park"], d["id"], entries=[])


if __name__ == "__main__":
    unittest.main()
