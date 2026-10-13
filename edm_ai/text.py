from __future__ import annotations

import re

_TOKEN = re.compile(r"[0-9a-z]+(?:[-_][0-9a-z]+)*|[가-힣]+")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def tokenize(s: str) -> list[str]:
    """Language-light tokenizer: lowercase words + Hangul character bigrams.
    Bigrams make Korean josa/eomi variants ('한빛전자의') match their stems without a morphological analyzer."""
    out: list[str] = []
    for m in _TOKEN.finditer(s.lower()):
        w = m.group()
        if "가" <= w[0] <= "힣":
            if len(w) >= 2:
                out.append(w)
                out.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            out.append(w)
            out.extend(p for p in re.split(r"[-_]", w) if p != w)
    return out


def sentences(s: str) -> list[str]:
    return [x.strip() for x in _SENT.split(s) if x.strip()]


def chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """Paragraph-aware chunking; markdown headings are carried into following chunks as context."""
    chunks: list[str] = []
    heading, buf = "", ""
    text = re.sub(r"^(#+ .*)$", r"\1\n", text, flags=re.M)  # headings always form their own block
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#") and "\n" not in para:
            if buf:
                chunks.append(buf)
                buf = ""
            heading = para.lstrip("# ").strip()
            continue
        if buf and len(buf) + len(para) > max_chars:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n{para}" if buf else (f"[{heading}] {para}" if heading else para)
    if buf:
        chunks.append(buf)
    return chunks
