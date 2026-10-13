from __future__ import annotations

import csv
import fnmatch
import io
import json
import subprocess
import zipfile
from xml.etree import ElementTree as ET
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from .entities import EntityExtractor
from .models import Acl
from .parsers import OFFICE, Unsupported
from .store import DocumentStore
from .text import chunk_text

SUPPORTED = {".md", ".txt", ".csv", ".eml", ".html", ".htm"} | set(OFFICE)


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style")
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "br"):
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def read_file(path: Path) -> tuple[str, str]:
    """Return (title, plain text). Open formats only; plug in OCR/PDF/Office converters here."""
    ext = path.suffix.lower()
    if ext in OFFICE:
        return path.stem, OFFICE[ext](path)
    if ext == ".eml":
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        body = msg.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body else ""
        head = f"보낸사람: {msg['from']}\n받는사람: {msg['to']}\n날짜: {msg['date']}"
        return str(msg["subject"] or path.stem), f"{head}\n\n{text}"
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        s = _Strip()
        s.feed(raw)
        return path.stem, "".join(s.parts)
    if ext == ".csv":
        rows = list(csv.DictReader(io.StringIO(raw)))
        return path.stem, "\n\n".join(", ".join(f"{k}={v}" for k, v in r.items()) for r in rows)
    first = next((ln.lstrip("# ").strip() for ln in raw.splitlines() if ln.startswith("# ")), path.stem)
    return first, raw


def ingest_dir(root: Path, manifest_path: Path | None, store: DocumentStore, extractor: EntityExtractor) -> dict:
    """Crawl a folder. ACLs come from the manifest (glob -> acl); the first matching rule wins and
    'default' applies otherwise. Unlisted files get the default (fail-closed if default is restrictive)."""
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path and manifest_path.exists() else {}
    rules = manifest.get("rules", [])
    default = Acl.from_dict(manifest.get("default", {"roles": ["admin"], "level": 99}))
    stats = {"added": 0, "unchanged": 0, "pruned": 0, "skipped": []}
    seen = set()
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SUPPORTED or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        seen.add(rel)
        acl = next((Acl.from_dict(r["acl"]) for r in rules if fnmatch.fnmatch(rel, r["glob"])), default)
        try:
            title, text = read_file(path)
        except (Unsupported, zipfile.BadZipFile, OSError, ET.ParseError, subprocess.SubprocessError) as e:
            stats["skipped"].append(f"{rel}: {e}")  # never index half-parsed content
            seen.discard(rel)
            continue
        pieces = [(c, extractor.extract(f"{title}\n{c}")) for c in chunk_text(text)]
        stats["added" if store.upsert(rel, title, acl, pieces, text) else "unchanged"] += 1
    stats["pruned"] = store.prune(seen)
    return stats
