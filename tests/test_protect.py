import http.server
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_platform  # noqa: E402
from edm_ai import crypt  # noqa: E402
from edm_ai.errors import DmsError  # noqa: E402


class Crypt(unittest.TestCase):
    def test_roundtrip_tamper_wrongkey(self):
        k = b"k" * 32
        for data in (b"", b"a", os.urandom(1000), "한글 본문".encode() * 500):
            blob = crypt.seal(data, k)
            self.assertTrue(crypt.is_sealed(blob))
            self.assertEqual(crypt.open_(blob, k), data)
        blob = crypt.seal(b"secret text", k)
        self.assertNotIn(b"secret", blob)
        self.assertNotEqual(crypt.seal(b"x", k), crypt.seal(b"x", k))                 # fresh nonce
        bad = bytearray(blob)
        bad[30] ^= 1
        with self.assertRaises(ValueError):
            crypt.open_(bytes(bad), k)
        with self.assertRaises(ValueError):
            crypt.open_(blob, b"z" * 32)


class Protect(test_platform.DemoCase):
    def setUp(self):
        self.d, self.U = self.app.dms, self.app.users
        self.fid = self.d.q1("SELECT id FROM folders WHERE name='MICE유치팀'")["id"]

    def test_encrypted_at_rest_but_searchable_and_downloadable(self):
        self.d._storage_key = b"unit-test-storage-key-123456"
        try:
            doc = self.d.register(self.U["kim"], "e.md", "# 암호 시험\n\n암호화본문 enctoken".encode(), folder_id=self.fid, doc_type="GEN", title="암호 시험")
            v = self.d._ver(self.d.row(doc["id"]), 1)
            raw = (self.d.repo / v["path"]).read_bytes()
            self.assertTrue(crypt.is_sealed(raw))
            self.assertNotIn("암호화본문".encode(), raw)
            self.assertIn("enctoken", v["text"])                                       # text extracted before sealing -> searchable
            self.assertEqual(self.d.download(self.U["kim"], doc["id"])[1], "# 암호 시험\n\n암호화본문 enctoken".encode())
            self.d._storage_key = None
            with self.assertRaises(DmsError):
                self.d.download(self.U["kim"], doc["id"])                               # key missing -> refuse, never serve ciphertext
        finally:
            self.d._storage_key = None

    def test_encrypted_office_file_is_extracted(self):
        import io
        import zipfile
        from test_trace import DOC
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", DOC % "암호화워드본문")
        self.d._storage_key = b"unit-test-storage-key-654321"
        try:
            doc = self.d.register(self.U["kim"], "w.docx", b.getvalue(), folder_id=self.fid, doc_type="GEN", title="워드 암호")
            self.assertIn("암호화워드본문", self.d._ver(self.d.row(doc["id"]), 1)["text"])
        finally:
            self.d._storage_key = None

    def test_text_watermark(self):
        self.app.cfg["protection"] = {"watermark": True}
        try:
            doc = self.d.register(self.U["kim"], "w.md", b"# wm\n\nwatermark body", folder_id=self.fid, doc_type="GEN", title="워터마크")
            name, data, _ = self.d.download(self.U["kim"], doc["id"])
            self.assertIn("다운로드 kim", data.decode())
            self.assertIn("watermark body", data.decode())
            self.assertEqual(self.d.read_bytes(self.d._ver(self.d.row(doc["id"]), 1)), b"# wm\n\nwatermark body")       # stored copy untouched
        finally:
            self.app.cfg.pop("protection")

    def test_external_drm_hook_and_fail_closed(self):
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                seen.update(doc=self.headers["X-EDM-Doc"], user=self.headers["X-EDM-User"], n=len(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"DRM:" + body)

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        doc = self.d.register(self.U["kim"], "d.md", b"# drm\n\ndrm body", folder_id=self.fid, doc_type="GEN", title="DRM")
        try:
            self.app.cfg["protection"] = {"drm": {"url": f"http://127.0.0.1:{srv.server_address[1]}/pack", "min_level": 0}}
            name, data, _ = self.d.download(self.U["kim"], doc["id"])
            self.assertTrue(data.startswith(b"DRM:# drm"))
            self.assertEqual((seen["user"], seen["doc"]), ("kim", doc["doc_no"]))
            srv.shutdown()
            srv.server_close()
            with self.assertRaises(DmsError) as cm:
                self.d.download(self.U["kim"], doc["id"])
            self.assertEqual(cm.exception.code, 502)                                      # server down -> no unprotected copy
        finally:
            self.app.cfg.pop("protection", None)


if __name__ == "__main__":
    unittest.main()
