from __future__ import annotations

import time

from .llm import citations_valid, numbers_grounded
from .vectors import dot

EVIDENCE = ("[E1] (계약 요약) 계약번호는 CT-9999-001이다. 계약금액은 120,000,000원이고 납기는 2025-06-30이다.\n\n"
            "[E2] (메모) 담당자는 검수 일정을 협의하기로 했다.")
SEMANTIC = ("차량 정비 일정 안내", "자동차 수리 스케줄 공지", "구내식당 점심 메뉴 안내")


def run_check(app) -> tuple[list[tuple[str, str]], bool]:
    """Field check for a configured model server. PASS / WARN / FAIL; exit code is non-zero only on FAIL.
    WARN means 'works but you should look' (slow, or the embedding model is not semantic)."""
    out: list[tuple[str, str]] = []
    eng = app.engine

    def add(level, msg):
        out.append((level, msg))

    if not eng.llm and not eng.embedder:
        add("FAIL", "설정에 llm / embedder 가 없습니다 (config.json 의 \"llm\", \"embedder\" 참고)")
        return out, False

    if eng.llm:
        t0 = time.time()
        try:
            text = eng.llm.complete("계약금액과 납기는?", EVIDENCE)
        except Exception as e:
            add("FAIL", f"LLM 호출 실패: {type(e).__name__}: {e}")
        else:
            dt = time.time() - t0
            add("PASS", f"LLM 응답 수신 ({dt:.1f}s): {text[:80]!r}")
            add("WARN" if dt > 10 else "PASS", f"응답 지연 {dt:.1f}s (기준 10s)")
            add("PASS" if citations_valid(text, 2) else "FAIL", "[E#] 인용 형식 (없거나 범위 밖이면 서비스에서 폐기됨)")
            bad = numbers_grounded(text, [EVIDENCE])
            add("PASS" if not bad else "FAIL", "숫자 근거 일치" if not bad else f"근거에 없는 숫자 생성: {bad}")
            facts = "120,000,000" in text.replace("억 2천만원", "") or "1억 2천만" in text or "120000000" in text
            add("PASS" if facts else "WARN", "핵심 사실(계약금액) 포함" if facts else "계약금액을 답에 포함하지 않음")

    if eng.embedder:
        t0 = time.time()
        try:
            v = eng.embedder.embed(list(SEMANTIC))
        except Exception as e:
            add("FAIL", f"임베딩 호출 실패: {type(e).__name__}: {e}")
        else:
            dt = time.time() - t0
            add("PASS", f"임베딩 수신 ({dt:.1f}s), 차원 {len(v[0])}")
            norm = sum(x * x for x in v[0]) ** 0.5
            add("PASS" if abs(norm - 1) < 1e-3 else "FAIL", f"벡터 정규화 (노름 {norm:.3f})")
            s_close, s_far = dot(v[0], v[1]), dot(v[0], v[2])
            add("PASS" if s_close > s_far + 0.05 else "WARN",
                f"의미 유사도: 유의어쌍 {s_close:.2f} vs 무관쌍 {s_far:.2f}" +
                ("" if s_close > s_far + 0.05 else "  → 의미 검색 품질이 낮습니다 (해시/어휘 기반 모델일 수 있음)"))
            dm = app.engine.index.dense_min
            add("PASS" if s_far < dm <= s_close else "WARN",
                f"dense_min={dm}: 유의어쌍({s_close:.2f})은 통과하고 무관쌍({s_far:.2f})은 걸러야 합니다. "
                + ("적절합니다." if s_far < dm <= s_close else "이 모델에 맞게 조정하고 eval 로 확인하세요."))
    return out, not any(l == "FAIL" for l, _ in out)
