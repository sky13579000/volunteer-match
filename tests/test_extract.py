import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fixtures_ole as fx  # noqa: E402

from edm_ai import pdftext  # noqa: E402
from edm_ai.ingest import read_file  # noqa: E402
from edm_ai.msg import parse_msg  # noqa: E402
from edm_ai.parsers import Unsupported  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731


class PdfPure(unittest.TestCase):
    def test_korean_pdf_from_libreoffice_matches_poppler(self):
        data = (FIX / "reg_ko.pdf").read_bytes()
        text = pdftext.extract_text(data)
        self.assertIn("제주국제컨벤션센터 시설 사용 규정", text)
        self.assertIn(norm("지체상금은 하루 0.1%이다"), norm(text))
        self.assertIn("CT-2024-001", text)
        self.assertEqual(len(text.splitlines()), 4)                                  # one text line per visual line
        import shutil
        if shutil.which("pdftotext"):
            ref = subprocess.run(["pdftotext", "-layout", str(FIX / "reg_ko.pdf"), "-"], capture_output=True, text=True).stdout
            self.assertEqual(norm(text), norm(ref))

    def test_multi_page_pdf_with_table(self):
        text = pdftext.extract_text((FIX / "safety_ko.pdf").read_bytes())
        self.assertIn("비상구 확보", text)
        self.assertIn(norm("79번째 문단입니다"), norm(text))                            # last paragraph is on a later page
        self.assertLess(text.index("1번째 문단"), text.index("79번째 문단"))              # page order is kept

    def test_read_file_uses_the_pure_extractor_when_no_tools_are_present(self):
        import edm_ai.parsers as P
        old = P.shutil.which
        P.shutil.which = lambda *_: None                                              # simulate a Windows PC without poppler
        try:
            title, text = read_file(FIX / "reg_ko.pdf")
        finally:
            P.shutil.which = old
        self.assertIn("제주국제컨벤션센터", text)

    def build_objstm_pdf(self, encrypted=False) -> bytes:
        """A PDF whose page tree lives inside a compressed object stream, with a Type0 font and a ToUnicode map."""
        cmap = b"/CIDInit /ProcSet findresource begin begincmap 3 beginbfchar <0001> <C81C> <0002> <C8FC> endbfchar 1 beginbfrange <0003> <0004> <D589> <D5CB> endbfrange endcmap"
        content = zlib.compress(b"BT /F1 12 Tf 72 700 Td <00010002> Tj 0 -20 Td [<0003> -400 <0004>] TJ ET")
        objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /Font << /F1 5 0 R >> >> >>",
                3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>", 5: b"<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H /ToUnicode 6 0 R >>"}
        body, offs = b"", []
        for n, o in objs.items():
            offs.append((n, len(body)))
            body += o + b"\n"
        head = " ".join(f"{n} {off}" for n, off in offs).encode() + b"\n"
        objstm = zlib.compress(head + body)
        out = bytearray(b"%PDF-1.5\n")
        out += b"7 0 obj\n<< /Type /ObjStm /N 4 /First " + str(len(head)).encode() + b" /Filter /FlateDecode /Length " + str(len(objstm)).encode() + b" >>\nstream\n" + objstm + b"\nendstream\nendobj\n"
        out += b"4 0 obj\n<< /Filter /FlateDecode /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream\nendobj\n"
        cm = zlib.compress(cmap)
        out += b"6 0 obj\n<< /Filter /FlateDecode /Length " + str(len(cm)).encode() + b" >>\nstream\n" + cm + b"\nendstream\nendobj\n"
        out += b"trailer\n<< /Root 1 0 R" + (b" /Encrypt 9 0 R" if encrypted else b"") + b" >>\n%%EOF"
        return bytes(out)

    def test_object_streams_type0_fonts_and_cmap_ranges(self):
        text = pdftext.extract_text(self.build_objstm_pdf())
        self.assertEqual(text.splitlines()[0], "제주")                                    # bfchar mapping, 2-byte codes, page found via object stream
        self.assertEqual(text.splitlines()[1], chr(0xD589) + " " + chr(0xD58A))           # bfrange mapping + TJ spacing

    def test_encrypted_and_garbage_pdfs_are_refused_cleanly(self):
        with self.assertRaises(pdftext.PdfError):
            pdftext.extract_text(self.build_objstm_pdf(encrypted=True))
        with self.assertRaises(pdftext.PdfError):
            pdftext.extract_text(b"this is not a pdf")
        # a scanned (image-only) PDF has no text layer: extraction is empty, never an exception
        scanned = b"%PDF-1.4\n1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n3 0 obj<< /Type /Page /Parent 2 0 R >>endobj\n%%EOF"
        self.assertEqual(pdftext.extract_text(scanned), "")

    def test_scanned_pdf_is_stored_without_text_instead_of_rejecting_the_upload(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4\n1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n3 0 obj<< /Type /Page /Parent 2 0 R >>endobj\n%%EOF")
        tmp.close()
        with self.assertRaises(Unsupported):
            read_file(Path(tmp.name))


class Msg(unittest.TestCase):
    def build(self):
        u = lambda s: s.encode("utf-16-le")  # noqa: E731
        streams = {
            "__substg1.0_0037001F": u("[EVT-2024] 대관 일정 확인"), "__substg1.0_1000001F": u("안녕하세요. 11월 대관 일정을 첨부합니다."),
            "__substg1.0_0C1A001F": u("김유치"), "__substg1.0_0C1F001F": u("kim@iccjeju.example"), "__substg1.0_0E04001F": u("park@iccjeju.example"),
            "__attach_version1.0_#00000000/__substg1.0_3707001F": u("일정표.md"), "__attach_version1.0_#00000000/__substg1.0_37010102": "# 일정표\n\n11월 10일".encode(),
        }
        return fx.build_ole(streams)

    def test_headers_body_and_attachment(self):
        m = parse_msg(self.build())
        self.assertEqual((m["subject"], m["sender"], m["sender_email"]), ("[EVT-2024] 대관 일정 확인", "김유치", "kim@iccjeju.example"))
        self.assertIn("11월 대관 일정", m["body"])
        self.assertEqual(m["attachments"], [("일정표.md", "# 일정표\n\n11월 10일".encode())])

    def test_read_file_indexes_a_msg(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.msg"
            p.write_bytes(self.build())
            title, text = read_file(p)
        self.assertIn("대관 일정 확인", text)
        self.assertIn("첨부: 일정표.md", text)

    def test_non_msg_ole_is_refused(self):
        with self.assertRaises(Unsupported):
            parse_msg(fx.build_ole({"Foo": b"bar"}))


if __name__ == "__main__":
    unittest.main()
