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
from .store import MANAGED_PREFIX, DocumentStore
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


class Root:
    """One crawl root: a folder plus how its files map to source ids and ACLs."""

    def __init__(self, path: Path, prefix: str = "", rules=None, default: Acl | None = None, include=None, exclude=None, max_bytes: int = 0, name: str = ""):
        self.path, self.prefix, self.rules = Path(path), prefix, rules or []
        self.default = default or Acl.from_dict({"roles": ["admin"], "level": 99})
        self.include, self.exclude, self.max_bytes, self.name = include, exclude or [], max_bytes, name


def _matches(rel: str, pats) -> bool:
    low = rel.lower()
    base = low.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(low, p.lower()) or fnmatch.fnmatch(base, p.lower()) for p in pats)


def ingest_roots(roots: list[Root], store: DocumentStore, extractor: EntityExtractor, dry_run: bool = False) -> dict:
    """Crawl several roots into one index (a source id is prefix + relative path). Pruning is global, so a vanished file
    disappears from search, but nothing is pruned when a root is unreachable (an offline NAS must not wipe the index)."""
    stats = {"added": 0, "unchanged": 0, "pruned": 0, "skipped": [], "roots": {}}
    seen: set = set()
    overrides = store.acl_overrides()   # ACLs edited in the UI take precedence over manifest rules
    complete = True
    for root in roots:
        rs = stats["roots"].setdefault(root.prefix or "/", {"added": 0, "unchanged": 0, "skipped": [], "error": ""})
        if not root.path.is_dir():
            rs["error"] = f"경로에 접근할 수 없습니다: {root.path}"
            complete = False
            continue
        for path in sorted(root.path.rglob("*")):
            try:
                if path.suffix.lower() not in SUPPORTED or not path.is_file():
                    continue
                rel = path.relative_to(root.path).as_posix()
                if root.include and not _matches(rel, root.include):
                    continue
                if root.exclude and _matches(rel, root.exclude):
                    continue
                if root.max_bytes and path.stat().st_size > root.max_bytes:
                    rs["skipped"].append(f"{rel}: 파일이 너무 큽니다")
                    continue
            except OSError as e:
                rs["skipped"].append(f"{path.name}: {e}")
                continue
            sid = root.prefix + rel
            seen.add(sid)
            acl = overrides.get(sid) or next((Acl.from_dict(r["acl"]) for r in root.rules if fnmatch.fnmatch(rel, r["glob"])), root.default)
            try:
                title, text = read_file(path)
            except (Unsupported, zipfile.BadZipFile, OSError, ET.ParseError, subprocess.SubprocessError) as e:
                rs["skipped"].append(f"{rel}: {e}")  # never index half-parsed content
                stats["skipped"].append(f"{sid}: {e}")
                seen.discard(sid)
                continue
            pieces = [(c, extractor.extract(f"{title}\n{c}")) for c in chunk_text(text)]
            if dry_run:
                changed = store.would_change(sid, acl, text)
            else:
                changed = store.upsert(sid, title, acl, pieces, text)
            key = "added" if changed else "unchanged"
            stats[key] += 1
            rs[key] += 1
            if changed and not dry_run:
                store.add_history(sid, "system", "indexed", f"{len(pieces)} chunks")
    if complete and not dry_run:
        stats["pruned"] = store.prune(seen)
    elif complete:
        stats["pruned"] = sum(1 for r in store.db.execute("SELECT source FROM documents") if r[0] not in seen and not r[0].startswith(MANAGED_PREFIX))
    return stats


def ingest_dir(root: Path, manifest_path: Path | None, store: DocumentStore, extractor: EntityExtractor) -> dict:
    """Crawl a folder. ACLs come from the manifest (glob -> acl); the first matching rule wins and
    'default' applies otherwise. Unlisted files get the default (fail-closed if default is restrictive)."""
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path and manifest_path.exists() else {}
    return ingest_roots([Root(root, "", manifest.get("rules", []), Acl.from_dict(manifest.get("default", {"roles": ["admin"], "level": 99})))], store, extractor)
