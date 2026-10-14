from __future__ import annotations

import re

from .text import sentences

# ---------- Korean money / date parsing ----------
_SUB = {"천": 1000, "백": 100, "십": 10}                 # units inside one 4-digit block
_BIG = {"조": 10**12, "억": 10**8, "만": 10**4}          # block multipliers
_TOK = re.compile(r"(\d[\d,]*(?:\.\d+)?)?\s*([조억만천백십])")
_MONEY = re.compile(r"((?:(?:\d[\d,]*(?:\.\d+)?)?\s*[조억만천백십]\s*)+)(\d[\d,]*)?\s*원|(\d[\d,]*(?:\.\d+)?)\s*원")
_DATE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?")


def _value(m) -> int:
    if m.group(3) is not None:
        return int(round(float(m.group(3).replace(",", ""))))
    total = block = 0.0
    for num, unit in _TOK.findall(m.group(1)):
        n = float(num.replace(",", "")) if num else None
        if unit in _SUB:
            block += (n if n is not None else 1) * _SUB[unit]
        else:
            total += ((block + n) if n is not None else (block or 1)) * _BIG[unit]
            block = 0.0
    total += block + (float(m.group(2).replace(",", "")) if m.group(2) else 0)
    return int(round(total))


def money_spans(text: str) -> list[tuple[int, int, int]]:
    """(start, end, won) for every amount phrase: '4억 8천만원', '3천5백만원', '480,000,000원', '1.2억원'.
    Blocks: sub-units (천 백 십) accumulate into a block that a big unit (만 억 조) then scales."""
    return [(m.start(), m.end(), _value(m)) for m in _MONEY.finditer(text)]


def parse_krw(text: str) -> list[int]:
    return [v for _, _, v in money_spans(text)]


def parse_dates(text: str) -> list[str]:
    return [f"{y}-{int(mo):02d}-{int(d):02d}" for y, mo, d in _DATE.findall(text)]


# ---------- classification / summary ----------
CATEGORIES = {
    "계약서": ["계약", "계약금액", "지체상금", "검수", "갑", "을"],
    "회의록": ["회의", "결정 사항", "안건", "참석"],
    "이메일": ["보낸사람", "받는사람", "회신", "드립니다"],
    "재무/원가": ["원가", "마진", "인건비", "외주", "예산", "청구"],
    "인사": ["성과", "평가", "보상", "급여", "채용"],
    "도면/기술": ["도면", "공차", "rev.", "설계", "ISO"],
}


def classify(title: str, text: str) -> str:
    blob = f"{title}\n{text}"
    scores = {c: sum(blob.count(k) for k in kws) for c, kws in CATEGORIES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "기타"


def summarize(text: str, n: int = 2) -> str:
    body = [s for s in sentences(re.sub(r"^\[.*?\]\s*", "", text, flags=re.M)) if len(s) > 12 and not s.startswith("#")]
    return " ".join(body[:n])


# ---------- contract field extraction ----------
def extract_fields(text: str) -> dict:
    f: dict = {}
    m = re.search(r"CT-\d{4}-\d{3}", text)
    if m:
        f["contract_no"] = m.group()
    m = re.search(r"PRJ-\d{4}", text)
    if m:
        f["project"] = m.group()
    for s in sentences(text):
        if "amount" not in f and re.search(r"계약\s*금액|총\s*액|계약\s*대금", s):
            v = parse_krw(s)
            if v:
                f["amount"] = v[0]
        if "due_date" not in f and re.search(r"납기|납품|준공|완료\s*예정|계약\s*종료", s):
            d = parse_dates(s)
            if d:
                f["due_date"] = d[-1]
    return f


def analyze(principal, title: str, text: str, gateway, cfg: dict | None) -> dict:
    """Classify, summarise, extract contract fields and cross-check them against the structured DB.
    The DB side goes through the SqlGateway with the CALLER's permissions: a column the caller cannot read
    is never compared, and rows they cannot read are never revealed through a 'mismatch' message."""
    fields = extract_fields(text)
    result = {"category": classify(title, text), "summary": summarize(text), "fields": fields,
              "checks": [], "findings": []}
    if not (gateway and cfg and (fields.get("contract_no") or fields.get("project"))):
        return result
    ents = {f"contract:{fields['contract_no']}"} if fields.get("contract_no") else set()
    if fields.get("project"):
        ents.add(f"project:{fields['project']}")
    for row in gateway.lookup(principal, ents):
        if row["table"] != cfg["table"]:
            continue
        if fields.get("contract_no") and str(row["key"]) != fields["contract_no"]:
            continue
        for fld, col, label in (("amount", cfg.get("amount_col"), "계약금액"), ("due_date", cfg.get("due_col"), "납기")):
            if fld not in fields or not col or col in row["masked"]:
                continue
            dbv = row["values"].get(col)
            dbv = int(dbv) if fld == "amount" else str(dbv)
            ok = dbv == fields[fld]
            result["checks"].append({"field": label, "doc": fields[fld], "db": dbv, "status": "ok" if ok else "mismatch",
                                     "row": f"{row['table']}:{row['key']}"})
            if not ok:
                result["findings"].append({
                    "severity": "high" if fld == "amount" else "medium", "field": label,
                    "message": f"문서의 {label} {fields[fld]:,}원이 DB 값 {dbv:,}원과 다릅니다." if fld == "amount"
                               else f"문서의 {label} {fields[fld]}이(가) DB 값 {dbv}과(와) 다릅니다.",
                    "row": f"{row['table']}:{row['key']}"})
    return result
