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
    """Text-layer PDFs: poppler `pdftotext` if installed, then `pypdf` if installed, then the built-in pure-Python extractor
    (works on a plain Windows PC). Scanned PDFs need OCR (EDM_AI_OCR=1 + tesseract); otherwise Unsupported."""
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
    except Exception:
        pass
    from . import pdftext
    try:
        text = pdftext.extract_text(Path(path).read_bytes())
        if text.strip():
            return text
    except pdftext.PdfError as e:
        if "encrypted" in str(e):
            raise Unsupported("암호가 걸린 PDF 입니다 (암호를 해제한 파일을 올려 주세요)")
    from . import ocr
    if ocr.enabled():
        text = ocr.ocr_pdf(Path(path))
        if text.strip():
            return text
    raise Unsupported("텍스트를 추출할 수 없는 PDF 입니다 (스캔 이미지일 수 있습니다 - OCR 설정 시 읽을 수 있습니다)")


def read_hwp_any(path: Path) -> str:
    from .hwp import read_hwp
    return read_hwp(path)


def read_hwpx_any(path: Path) -> str:
    from .hwp import read_hwpx
    return read_hwpx(path)


def read_msg_any(path: Path) -> str:
    from .msg import read_msg
    return read_msg(path)


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def read_via_libreoffice(path: Path) -> str:
    """Legacy binary Office files (.doc/.xls/.ppt/.rtf): converted to text by LibreOffice when it is installed on the server."""
    exe = _soffice()
    if not exe:
        raise Unsupported("이 형식은 서버에 LibreOffice 가 있어야 읽을 수 있습니다 (.docx/.xlsx/.pptx 로 저장해 올리면 바로 읽힙니다)")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        filt = "txt:Text (encoded):UTF8" if path.suffix.lower() in (".doc", ".rtf", ".ppt") else "csv:Text - txt - csv (StarCalc):44,34,76"
        ext = "txt" if filt.startswith("txt") else "csv"
        try:
            subprocess.run([exe, "--headless", "--norestore", f"-env:UserInstallation=file://{td}/profile", "--convert-to", filt if ext == "txt" else "csv", "--outdir", td, str(path)],
                           capture_output=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            raise Unsupported("LibreOffice 변환 시간이 초과되었습니다")
        out = next(Path(td).glob(f"*.{ext}"), None)
        if not out:
            raise Unsupported("LibreOffice 로 변환하지 못했습니다")
        return out.read_text("utf-8", errors="replace")


OFFICE = {".docx": read_docx, ".pptx": read_pptx, ".xlsx": read_xlsx,
          ".odt": read_odf, ".ods": read_odf, ".odp": read_odf, ".pdf": read_pdf,
          ".hwp": read_hwp_any, ".msg": read_msg_any, ".hwpx": read_hwpx_any, ".doc": read_via_libreoffice, ".xls": read_via_libreoffice, ".ppt": read_via_libreoffice, ".rtf": read_via_libreoffice}
