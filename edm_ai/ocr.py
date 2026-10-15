"""Optional OCR through the `tesseract` command line tool (and `pdftoppm` for PDFs). Off by default because OCR is slow:
set EDM_AI_OCR=1 (and install tesseract with the Korean data, e.g. `tesseract-ocr-kor`). Language: EDM_AI_OCR_LANG (default kor+eng)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def enabled() -> bool:
    return os.environ.get("EDM_AI_OCR", "") in ("1", "true", "yes") and bool(shutil.which("tesseract"))


def available_tools() -> dict:
    return {"tesseract": bool(shutil.which("tesseract")), "pdftoppm": bool(shutil.which("pdftoppm")), "enabled": enabled()}


def _tess(img: Path) -> str:
    r = subprocess.run(["tesseract", str(img), "stdout", "-l", os.environ.get("EDM_AI_OCR_LANG", "kor+eng"), "--psm", "3"], capture_output=True, text=True, timeout=180, check=False)
    return r.stdout if r.returncode == 0 else ""


def ocr_image(path: Path) -> str:
    return _tess(path).strip()


def ocr_pdf(path: Path, max_pages: int = 60) -> str:
    if not shutil.which("pdftoppm"):
        return ""
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "200", "-l", str(max_pages), "-png", str(path), str(Path(td) / "p")], capture_output=True, timeout=600, check=False)
        return "\n\n".join(t for t in (_tess(img).strip() for img in sorted(Path(td).glob("p*.png"))) if t)
