from __future__ import annotations

import re

PII = [
    (re.compile(r"\b\d{6}-[1-4]\d{6}\b"), "[주민번호]"),
    (re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"), "[전화번호]"),
    (re.compile(r"\b(?:\d{4}-){3}\d{4}\b"), "[카드번호]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[이메일]"),
]
INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts?)|disregard .{0,30}instructions|"
    r"you are now|system prompt|reveal .{0,30}(secret|password|prompt)|"
    r"이전.{0,10}(지시|명령|프롬프트).{0,10}(무시|잊)|시스템 ?프롬프트|지금부터 (너는|당신은))", re.I)


def redact_pii(text: str) -> str:
    for rx, repl in PII:
        text = rx.sub(repl, text)
    return text


def scrub_injection(text: str) -> tuple[str, int]:
    """Defence in depth for LLM input only: neutralise instruction-like lines inside retrieved documents."""
    n = 0
    out = []
    for line in text.splitlines():
        if INJECTION.search(line):
            n += 1
            line = "[필터됨: 지시문 의심 내용]"
        out.append(line)
    return "\n".join(out), n
