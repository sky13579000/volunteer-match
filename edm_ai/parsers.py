from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_MEMBER = 30 * 1024 * 1024  # zip-bomb guard (uncompressed bytes per member)


class Unsupported(Exception):
    pass


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    info = z.getinfo(name)
    if info.file_size > MAX_MEMBER:
        raise Unsupported(f"{name} too large")
    raw = z.read(name)
    if re.search(rb"<!(DOCTYPE|ENTITY)", raw[:4096], re.I):  # block XXE / billion-laughs (stdlib XML is not hardened)
        raise Unsupported("DTD/entity declarations are not allowed")
    return ET.fromstring(raw)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _texts(el: ET.Element, text_tag: str, para_tag: str) -> list[str]:
    paras = []
    for p in el.iter():
        if _local(p.tag) == para_tag:
            t = "".join(x.text or "" for x in p.iter() if _local(x.tag) == text_tag)
            if t.strip():
                paras.append(t.strip())
    return paras


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return "\n\n".join(_texts(_xml(z, "word/document.xml"), "t", "p"))


def read_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[0]))
        return "\n\n".join(f"[슬라이드 {i}] " + " ".join(_texts(_xml(z, n), "t", "p"))
                           for i, n in enumerate(slides, 1))


def read_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(x.text or "" for x in si.iter() if _local(x.tag) == "t")
                      for si in _xml(z, "xl/sharedStrings.xml") if _local(si.tag) == "si"]
        out = []
        for n in sorted(x for x in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", x)):
            rows = []
            for row in (e for e in _xml(z, n).iter() if _local(e.tag) == "row"):
                cells = []
                for c in (e for e in row if _local(e.tag) == "c"):
                    v = next((x for x in c if _local(x.tag) == "v"), None)
                    if v is None or v.text is None:
                        continue
                    cells.append(shared[int(v.text)] if c.get("t") == "s" and int(v.text) < len(shared) else v.text)
                if cells:
                    rows.append(", ".join(cells))
            out.append("\n\n".join(rows))
        return "\n\n".join(out)


def read_odf(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = _xml(z, "content.xml")
        paras = ["".join(e.itertext()).strip() for e in root.iter() if _local(e.tag) in ("p", "h")]
        return "\n\n".join(p for p in paras if p)


def read_pdf(path: Path) -> str:
    """Text-layer PDFs only: poppler `pdftotext` if installed, else `pypdf` if installed. Scans need OCR (not bundled)."""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
                           capture_output=True, text=True, timeout=120, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    try:
        from pypdf import PdfReader  # type: ignore
        text = "\n\n".join(pg.extract_text() or "" for pg in PdfReader(str(path)).pages)
        if text.strip():
            return text
    except ImportError:
        pass
    raise Unsupported("no PDF text extractor (install poppler-utils or pypdf), or scanned PDF needs OCR")


OFFICE = {".docx": read_docx, ".pptx": read_pptx, ".xlsx": read_xlsx,
          ".odt": read_odf, ".ods": read_odf, ".odp": read_odf, ".pdf": read_pdf}
