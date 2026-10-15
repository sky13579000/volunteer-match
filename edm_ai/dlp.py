"""Personal-data detection for documents (Korean identifiers). Only COUNTS leave this module - matched values are never
stored, logged or returned, so the findings list is safe to show to anybody who may see the document at all."""
from __future__ import annotations

import re
from datetime import date

LABEL = {"rrn": "주민등록번호", "alien": "외국인등록번호", "card": "신용·체크카드 번호", "passport": "여권번호", "phone": "휴대전화번호", "email": "이메일 주소"}
SEVERITY = {"rrn": "high", "alien": "high", "card": "high", "passport": "high", "phone": "medium", "email": "low"}

_RRN = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})([- ]?)([1-8])(\d{6})(?!\d)")
_CARD = re.compile(r"(?<![\d-])(?:\d{4}[- ]?){3}\d{3,4}(?![\d-])")
_PHONE = re.compile(r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_PASSPORT = re.compile(r"(?<![A-Za-z0-9])[MSROD]\d{8}(?![A-Za-z0-9])")


def luhn_ok(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            d -= 9 if d > 9 else 0
        s += d
        alt = not alt
    return s % 10 == 0


def _valid_birth(yy: str, mm: str, dd: str, g: str) -> bool:
    century = {"1": 1900, "2": 1900, "5": 1900, "6": 1900, "3": 2000, "4": 2000, "7": 2000, "8": 2000}[g]
    try:
        d = date(century + int(yy), int(mm), int(dd))
    except ValueError:
        return False
    return d <= date.today()


def _rrn_checksum(n: str) -> bool:
    w = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    return (11 - sum(int(a) * b for a, b in zip(n[:12], w)) % 11) % 10 == int(n[12])


def scan(text: str) -> dict[str, int]:
    """{kind: count}. A number only counts when it is structurally valid (birth date, Luhn, checksum), which keeps false
    alarms on order numbers, amounts and phone-like codes low."""
    out: dict[str, int] = {}
    t = text or ""
    for m in _RRN.finditer(t):
        yy, mm, dd, sep, g, rest = m.groups()
        if not _valid_birth(yy, mm, dd, g):
            continue
        digits = yy + mm + dd + g + rest
        # numbers issued since 2020 are random: accept the hyphenated form on a valid birth date, or any form with a valid checksum
        if sep or _rrn_checksum(digits):
            kind = "alien" if g in "5678" else "rrn"
            out[kind] = out.get(kind, 0) + 1
    for m in _CARD.finditer(t):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits) and not _RRN.fullmatch(m.group()):
            out["card"] = out.get("card", 0) + 1
    n = len(_PHONE.findall(t))
    if n:
        out["phone"] = n
    n = len(_EMAIL.findall(t))
    if n:
        out["email"] = n
    n = len(_PASSPORT.findall(t))
    if n:
        out["passport"] = n
    return out


def describe(findings: dict[str, int]) -> list[dict]:
    return [{"kind": k, "label": LABEL[k], "count": n, "severity": SEVERITY[k]} for k, n in sorted(findings.items(), key=lambda kv: (SEVERITY[kv[0]] != "high", kv[0]))]


def summary(findings: dict[str, int], kinds=None) -> str:
    return ", ".join(f"{LABEL[k]} {n}건" for k, n in findings.items() if kinds is None or k in kinds)


# ---------------- masking ----------------
def _mask_rrn(m):
    return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}******"


def mask(text: str, kinds=None) -> str:
    """Replace detected identifiers with a masked form that keeps the shape (900101-1******, 010-****-5678, k***@host).
    Uses the same validity rules as scan(), so ordinary numbers are left alone."""
    want = set(kinds) if kinds else set(LABEL)
    t = text or ""
    spans = []                                                      # (start, end, replacement)
    if want & {"rrn", "alien"}:
        for m in _RRN.finditer(t):
            yy, mm, dd, sep, g, rest = m.groups()
            if _valid_birth(yy, mm, dd, g) and (sep or _rrn_checksum(yy + mm + dd + g + rest)) and ("alien" if g in "5678" else "rrn") in want:
                spans.append((m.start(), m.end(), _mask_rrn(m)))
    if "card" in want:
        for m in _CARD.finditer(t):
            digits = re.sub(r"\D", "", m.group())
            if 13 <= len(digits) <= 19 and luhn_ok(digits) and not _RRN.fullmatch(m.group()):
                spans.append((m.start(), m.end(), re.sub(r"\d", "*", m.group()[:-4]) + m.group()[-4:]))
    if "phone" in want:
        for m in _PHONE.finditer(t):
            g = m.group()
            spans.append((m.start(), m.end(), re.sub(r"\d(?=[-. ]?\d{4}$)", "*", g) if False else re.sub(r"(?<=\d)\d(?=\d*[-. ]?\d{4}$)", "*", g)))
    if "email" in want:
        for m in _EMAIL.finditer(t):
            local, _, host = m.group().partition("@")
            spans.append((m.start(), m.end(), local[:1] + "***@" + host))
    if "passport" in want:
        for m in _PASSPORT.finditer(t):
            spans.append((m.start(), m.end(), m.group()[0] + "********"))
    out, pos = [], 0
    for a, b, r in sorted(spans):
        if a < pos:
            continue
        out.append(t[pos:a])
        out.append(r)
        pos = b
    out.append(t[pos:])
    return "".join(out)
