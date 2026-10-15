"""Organisation logo. The logo file is uploaded by an administrator (Admin > Backup / License / Branding) and stored in <config dir>/branding/.
Raster images are checked by their magic bytes; SVG is refused if it contains anything executable or external. It is only ever shown through <img>."""
from __future__ import annotations

import re
from pathlib import Path

from .errors import DmsError

MAX = {"logo": 1024 * 1024, "hero": 3 * 1024 * 1024}
MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff", "gif": b"GIF8", "webp": b"RIFF"}
CTYPE = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}
BAD_SVG = re.compile(rb"<script|on\w+\s*=|javascript:|<foreignobject|<iframe|<embed|<object|xlink:href\s*=\s*[\"']?(?!#)|href\s*=\s*[\"']?https?:|@import|url\(\s*[\"']?https?:", re.I)


class Branding:
    def __init__(self, app):
        self.dir = app._p("branding")

    def _file(self, kind="logo") -> Path | None:
        for ext in CTYPE:
            p = self.dir / f"{kind}.{ext}"
            if p.is_file():
                return p
        return None

    def info(self) -> dict:
        f, h = self._file("logo"), self._file("hero")
        return {"logo": bool(f), "v": int(f.stat().st_mtime) if f else 0, "hero": bool(h), "hv": int(h.stat().st_mtime) if h else 0}

    def get(self, kind="logo") -> tuple[bytes, str] | None:
        f = self._file(kind)
        return (f.read_bytes(), CTYPE[f.suffix[1:]]) if f else None

    def save(self, filename: str, data: bytes, kind="logo") -> dict:
        if kind not in MAX:
            raise DmsError(400, "알 수 없는 이미지 종류입니다")
        ext = Path(str(filename)).suffix.lower().lstrip(".")
        ext = "jpg" if ext == "jpeg" else ext
        if ext not in CTYPE:
            raise DmsError(400, "PNG, JPG, GIF, WEBP, SVG 파일만 사용할 수 있습니다")
        if not data or len(data) > MAX[kind]:
            raise DmsError(400, f"파일은 {MAX[kind] // 1048576}MB 이하여야 합니다")
        if ext == "svg":
            if b"<svg" not in data[:4000].lower() or BAD_SVG.search(data):
                raise DmsError(400, "SVG에 스크립트·외부 링크가 있거나 올바른 SVG가 아닙니다. PNG로 저장해서 올려 주세요")
        elif not data.startswith(MAGIC[ext]) or (ext == "webp" and data[8:12] != b"WEBP"):
            raise DmsError(400, "파일 내용이 확장자와 맞지 않습니다")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.delete(kind)
        (self.dir / f"{kind}.{ext}").write_bytes(data)
        return self.info()

    def delete(self, kind="logo") -> dict:
        for ext in CTYPE:
            (self.dir / f"{kind}.{ext}").unlink(missing_ok=True)
        return self.info()
