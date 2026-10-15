"""Outlook .msg (OLE2 / MAPI) reader: subject, sender, recipients, date, plain-text body and attachments - standard library only."""
from __future__ import annotations

import re
from pathlib import Path

from .ole import Ole, OleError
from .parsers import Unsupported

_TAGS = {"subject": "0037", "body": "1000", "sender": "0C1A", "sender_email": "0C1F", "to": "0E04", "cc": "0E03", "html": "1013"}


def _prop(ole: Ole, entries: dict, prefix: str, tag: str) -> str | None:
    for typ, enc in (("001F", "utf-16-le"), ("001E", "cp949")):
        key = f"{prefix}__substg1.0_{tag}{typ}"
        if key in entries:
            return ole.read(key).decode(enc, "replace").replace("\x00", "")
    return None


def parse_msg(data: bytes) -> dict:
    try:
        ole = Ole(data)
        entries = ole.entries()
    except OleError as e:
        raise Unsupported(f"not a readable .msg file: {e}")
    if not any(k.startswith("__substg1.0_") or k.startswith("__properties") for k in entries):
        raise Unsupported("not an Outlook .msg file")
    out = {k: _prop(ole, entries, "", t) for k, t in _TAGS.items()}
    if not out["body"] and out["html"]:
        out["body"] = re.sub(r"<[^>]+>", " ", out["html"])
    atts = []
    for name in sorted(k for k in entries if k.startswith("__attach_version1.0_") and entries[k]["type"] == 1):
        fn = _prop(ole, entries, name + "/", "3707") or _prop(ole, entries, name + "/", "3704") or "attachment"
        key = f"{name}/__substg1.0_37010102"
        if key in entries:
            atts.append((re.sub(r"[\\/:*?\"<>|]", "_", fn), ole.read(key)))
    out["attachments"] = atts
    return out


def read_msg(path: Path) -> str:
    m = parse_msg(Path(path).read_bytes())
    head = f"보낸사람: {m['sender'] or ''} {('<' + m['sender_email'] + '>') if m['sender_email'] else ''}".rstrip()
    head += f"\n받는사람: {m['to'] or ''}" + (f"\n참조: {m['cc']}" if m["cc"] else "")
    if m["attachments"]:
        head += "\n첨부: " + ", ".join(n for n, _ in m["attachments"])
    return f"{m['subject'] or Path(path).stem}\n\n{head}\n\n{m['body'] or ''}"
