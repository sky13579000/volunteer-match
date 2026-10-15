import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai.dms_trace import read_tag, tag_bytes  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


DOC = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>'


def docx_like() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", DOC % "본문 trace")
    return b.getvalue()


class Trace(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]
        self.app.cfg["tracing"] = {"log_views": True, "tag_downloads": True}

    def tearDown(self):
        self.app.cfg.pop("tracing", None)

    def doc(self, text=b"# v1\n\ntrace body one", name="t.md"):
        return self.d.register(self.U["kim"], name, text, folder_id=self.fid, doc_type="GEN", title="추적 " + name)

    def test_tag_roundtrip_keeps_zip_and_pdf_valid(self):
        z = tag_bytes("a.docx", docx_like(), "GEN-2026-0001", 3)
        self.assertEqual(read_tag(z), ("GEN-2026-0001", 3))
        self.assertTrue(zipfile.ZipFile(io.BytesIO(z)).testzip() is None)
        self.assertIn("본문 trace".encode(), zipfile.ZipFile(io.BytesIO(z)).read("word/document.xml"))
        p = tag_bytes("a.pdf", b"%PDF-1.4\n...\n%%EOF\n", "X-1", 2)
        self.assertEqual(read_tag(p), ("X-1", 2))
        self.assertEqual(tag_bytes("a.md", b"hi", "X-1", 1), b"hi")                      # text is never altered

    def test_verify_latest_outdated_modified_unknown(self):
        v1 = b"# verify v1\n\ntrace body one"
        doc = self.doc(v1, "v.md")
        kim = self.U["kim"]
        r = self.d.verify_copy(kim, v1)
        self.assertEqual((r["state"], r["matches"][0]["ver"]), ("latest", 1))
        self.d.new_version(kim, doc["id"], "t.md", b"# verify v2\n\ntrace body two")
        r = self.d.verify_copy(kim, v1)
        self.assertEqual((r["state"], r["matches"][0]["latest_ver"]), ("outdated", 2))
        self.assertEqual(self.d.verify_copy(kim, b"# verify v2\n\ntrace body two")["state"], "latest")
        self.assertEqual(self.d.verify_copy(kim, b"never seen before")["state"], "unknown")
        self.assertEqual(self.d.verify_copy(kim, sha=r["sha256"])["state"], "outdated")                 # hash alone is enough
        self.assertEqual(self.d.verify_copy(self.U["choi"], v1)["state"], "unknown")                    # no oracle for documents the caller cannot read
        with self.assertRaises(DmsError):
            self.d.verify_copy(kim, sha="xyz")

    def test_delivered_copy_is_recognised_even_though_tagged(self):
        doc = self.d.register(self.U["kim"], "o.docx", docx_like(), folder_id=self.fid, doc_type="GEN", title="오피스 추적")
        name, data, _ = self.d.download(self.U["kim"], doc["id"])
        self.assertEqual(read_tag(data)[1], 1)
        r = self.d.verify_copy(self.U["kim"], data)
        self.assertEqual((r["state"], r["matches"][0]["doc_id"]), ("latest", doc["id"]))

    def test_modified_copy_with_tag_is_flagged(self):
        doc = self.d.register(self.U["kim"], "m.docx", docx_like(), folder_id=self.fid, doc_type="GEN", title="수정 사본")
        _, data, _ = self.d.download(self.U["kim"], doc["id"])
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", DOC % "changed")
            z.comment = f"EDM-ID:{doc['doc_no']}:v1".encode()
        r = self.d.verify_copy(self.U["kim"], b.getvalue())
        self.assertEqual(r["state"], "modified")

    def test_activity_log_dedupes_views_and_is_for_editors_only(self):
        doc = self.doc(name="act.md")
        self.d.get(self.U["kim"], doc["id"])
        self.d.get(self.U["kim"], doc["id"])
        self.d.download(self.U["kim"], doc["id"])
        a = self.d.doc_activity(self.U["kim"], doc["id"])
        self.assertEqual((a["totals"]["views"], a["totals"]["downloads"]), (1, 1))
        with self.assertRaises(DmsError):
            self.d.doc_activity(self.U["choi"], doc["id"])
        self.assertIn("다운로드", self.d.activity_csv(self.U["kim"], doc["id"]))

    def test_latest_share_follows_new_versions_pinned_one_does_not(self):
        doc = self.doc(b"# first\n\nshare one", "s.md")
        kim = self.U["kim"]
        pinned = self.d.create_share(kim, doc["id"], 3)
        latest = self.d.create_share(kim, doc["id"], 3, latest=True)
        self.d.new_version(kim, doc["id"], "s.md", b"# second\n\nshare two")
        with self.assertRaises(DmsError):
            self.d.share_info(pinned["token"])
        self.assertIn("share two", self.d.share_info(latest["token"])["text"])
        self.assertTrue(next(s for s in self.d.list_shares(kim, doc["id"]) if s["id"] == latest["id"])["latest"])


if __name__ == "__main__":
    unittest.main()
