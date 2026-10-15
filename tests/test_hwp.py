import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fixtures_ole as fx  # noqa: E402

from edm_ai import hwp  # noqa: E402
from edm_ai.ingest import read_file  # noqa: E402
from edm_ai.ole import Ole, OleError  # noqa: E402
from edm_ai.parsers import Unsupported  # noqa: E402


class OleReader(unittest.TestCase):
    def test_mini_and_regular_streams_round_trip(self):
        small, big = "작은 스트림".encode() * 3, bytes(range(256)) * 40
        o = Ole(fx.build_ole({"A": small, "Dir/B": big}))
        self.assertEqual(o.read("A"), small)
        self.assertEqual(o.read("Dir/B"), big)
        self.assertEqual(set(o.entries()), {"A", "Dir", "Dir/B"})

    def test_rejects_garbage_and_truncation(self):
        with self.assertRaises(OleError):
            Ole(b"not an ole file" * 100)
        good = fx.build_ole({"A": b"x" * 5000})
        with self.assertRaises(OleError):
            Ole(good[:600]).read("A")

    def test_hostile_fat_loop_terminates(self):
        data = bytearray(fx.build_ole({"A": b"y" * 6000}))
        o = Ole(bytes(data))
        o.fat[o.entries()["A"]["start"]] = o.entries()["A"]["start"]       # a sector chain pointing at itself
        out = o.read("A")                                                  # bounded: terminates (does not hang or exhaust memory)
        self.assertLessEqual(len(out), 6000)


class Hwp5(unittest.TestCase):
    def read(self, data: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as f:
            f.write(data)
        return read_file(Path(f.name))[1]

    def test_text_is_extracted_without_control_data(self):
        text = self.read(fx.build_hwp(["제주국제컨벤션센터 시설 사용 규정", "제2조 계약금은 사용료의 10%이다."]))
        self.assertIn("제주국제컨벤션센터 시설 사용 규정", text)
        self.assertIn("제2조 계약금은 사용료의 10%이다.", text)
        self.assertNotIn("\x02", text)
        self.assertEqual(text.count("\n\n"), 1)

    def test_uncompressed_and_large_documents(self):
        self.assertIn("비압축 문서", self.read(fx.build_hwp(["비압축 문서"], compressed=False)))
        big = self.read(fx.build_hwp(["첫 문단"], pad_paragraphs=300))                # forces the regular (non-mini) stream path
        self.assertIn("첫 문단", big)
        self.assertGreater(big.count("채움 문단"), 250)

    def test_password_protected_and_garbage_are_refused_cleanly(self):
        with self.assertRaises(Unsupported):
            self.read(fx.build_hwp(["비밀"], flags_extra=0x02))
        with self.assertRaises(Unsupported):
            self.read(b"MZ not a hangul file")

    def test_title_falls_back_to_the_file_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "2026_규정.hwp"
            p.write_bytes(fx.build_hwp(["본문"]))
            self.assertEqual(read_file(p)[0], "2026_규정")


class Hwpx(unittest.TestCase):
    def make(self, body: str) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d) / "a.hwpx"
        ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/hwp+zip")
            z.writestr("Contents/section0.xml", f'<?xml version="1.0" encoding="UTF-8"?><hs:sec {ns}>{body}</hs:sec>')
        return p

    def test_paragraph_text(self):
        p = self.make('<hp:p><hp:run><hp:t>행사 안전 점검표</hp:t></hp:run></hp:p><hp:p><hp:run><hp:t>비상구 </hp:t><hp:t>확인</hp:t></hp:run></hp:p>')
        text = hwp.read_hwpx(p)
        self.assertEqual(text, "행사 안전 점검표\n\n비상구 확인")
        self.assertEqual(read_file(p)[1], text)

    def test_entity_declarations_are_refused(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "x.hwpx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Contents/section0.xml", '<!DOCTYPE x [<!ENTITY a "b">]><sec/>')
        with self.assertRaises(Unsupported):
            hwp.read_hwpx(p)


if __name__ == "__main__":
    unittest.main()
