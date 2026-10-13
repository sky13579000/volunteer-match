from __future__ import annotations

import re


class EntityExtractor:
    """Finds business keys (project codes, customer names ...) in free text.
    These keys are the bridge between unstructured documents and structured DB rows."""

    def __init__(self, patterns: dict[str, str] | None = None, dictionaries: dict[str, list[str]] | None = None):
        self.patterns = {t: re.compile(p) for t, p in (patterns or {}).items()}
        self.dictionaries = {t: sorted({v for v in vals if v}, key=len, reverse=True)
                             for t, vals in (dictionaries or {}).items()}

    def extract(self, text: str) -> frozenset:
        found = set()
        for t, rx in self.patterns.items():
            found.update(f"{t}:{m.group().upper()}" for m in rx.finditer(text))
        for t, vals in self.dictionaries.items():
            found.update(f"{t}:{v}" for v in vals if v in text)
        return frozenset(found)
