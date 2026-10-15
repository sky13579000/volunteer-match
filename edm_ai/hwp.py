"""Text extraction for Hangul documents: .hwpx (zip + XML) and .hwp (HWP 5.x, OLE2 + zlib records).
Search and AI need the TEXT only; layout, tables structure and images are not reproduced."""
from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

from .ole import Ole, OleError
from .parsers import Unsupported

MAX_SECTION = 64 * 1024 * 1024
TAG_PARA_TEXT = 0x43                       # HWPTAG_BEGIN (0x10) + 51
# control characters that are followed by 7 more UTF-16 code units of control data
_EXTENDED = set(range(1, 9)) | {9, 11, 12} | set(range(14, 24))




def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_hwpx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = sorted((n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if not names:
            raise Unsupported("hwpx has no Contents/section*.xml")
        paras: list[str] = []
        for n in names:
            info = z.getinfo(n)
            if info.file_size > MAX_SECTION:
                raise Unsupported("hwpx section too large")
            raw = z.read(n)
            if re.search(rb"<!(DOCTYPE|ENTITY)", raw[:4096], re.I):
                raise Unsupported("DTD/entity declarations are not allowed")
            root = ET.fromstring(raw)
            for p in root.iter():
                if _local(p.tag) == "p":
                    parts = []
                    for t in p.iter():
                        ln = _local(t.tag)
                        if ln == "t":
                            parts.append("".join(t.itertext()))
                        elif ln in ("tab",):
                            parts.append("\t")
                        elif ln == "lineBreak":
                            parts.append("\n")
                    s = "".join(parts).strip()
                    if s:
                        paras.append(s)
    return "\n\n".join(paras)


def _records(data: bytes):
    i, n = 0, len(data)
    while i + 4 <= n:
        h = struct.unpack_from("<I", data, i)[0]
        tag, size = h & 0x3FF, (h >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:
            if i + 4 > n:
                return
            size = struct.unpack_from("<I", data, i)[0]
            i += 4
        if i + size > n:
            return
        yield tag, data[i: i + size]
        i += size


def _para_text(rec: bytes) -> str:
    out, i, n = [], 0, len(rec) // 2 * 2
    while i < n:
        ch = struct.unpack_from("<H", rec, i)[0]
        i += 2
        if ch >= 32:
            out.append(chr(ch))
        elif ch in (10, 13):
            out.append("\n")
        elif ch == 9:
            out.append("\t")
            i += 14
        elif ch in _EXTENDED:
            i += 14                                                  # inline/extended control: skip its data
    return "".join(out).replace("\x00", "")


def read_hwp(path: Path) -> str:
    data = Path(path).read_bytes()
    try:
        ole = Ole(data)
        header = ole.read("FileHeader")
    except OleError as e:
        raise Unsupported(f"not a readable HWP file: {e}")
    if not header.startswith(b"HWP Document File"):
        raise Unsupported("not an HWP 5.x document")
    flags = struct.unpack_from("<I", header, 36)[0] if len(header) >= 40 else 0
    if flags & 0x02:
        raise Unsupported("password-protected HWP documents cannot be read")
    compressed = bool(flags & 0x01)
    entries = ole.entries()
    if flags & 0x04 and not any(k.startswith("BodyText/") for k in entries):
        raise Unsupported("distribution-protected HWP (ViewText) cannot be read")
    sections = sorted((k for k in entries if re.fullmatch(r"BodyText/Section\d+", k)), key=lambda k: int(k[len("BodyText/Section"):]))
    if not sections:
        raise Unsupported("HWP has no BodyText sections")
    paras: list[str] = []
    for s in sections:
        raw = ole.read(s)
        if compressed:
            try:
                d = zlib.decompressobj(-15)
                raw = d.decompress(raw, MAX_SECTION)
            except zlib.error as e:
                raise Unsupported(f"corrupt HWP section: {e}")
        for tag, rec in _records(raw):
            if tag == TAG_PARA_TEXT:
                t = _para_text(rec).strip()
                if t:
                    paras.append(t)
    return "\n\n".join(paras)
