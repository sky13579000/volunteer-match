"""Seeds the demo repository with documents in realistic states (idempotent: runs only on an empty repository)."""
from datetime import date, timedelta


def seed(app):
    d, U = app.dms, app.users
    if d.q1("SELECT 1 AS x FROM dms_docs WHERE origin='managed' LIMIT 1"):
        return False
    fid = lambda name: d.q1("SELECT id FROM folders WHERE name=?", (name,))["id"]   # noqa: E731
    day = lambda n: (date.today() + timedelta(days=n)).isoformat()                  # noqa: E731
    md = lambda title, *paras: ("# " + title + "\n\n" + "\n\n".join(paras)).encode()  # noqa: E731
    with d.batch():
        # ---- 공용 규정: everybody may read; admin owns ----
        sec = d.register(U["admin"], "정보보안규정.md", md("사내 정보보안 규정", "## 목적", "임직원은 업무상 취득한 고객 정보와 문서를 외부에 유출하지 않는다. 비밀번호는 90일마다 변경한다.",
                         "## 문서 반출", "보안등급 2 이상 문서는 승인 없이 사외로 반출할 수 없으며 외부 공유 링크는 유효기간을 7일 이내로 한다."),
                         folder_id=fid("규정"), doc_type="GEN", tags=["규정", "보안"], description="전 직원 대상 정보보안 기본 규정")
        d.new_version(U["admin"], sec["id"], "정보보안규정.md", md("사내 정보보안 규정", "## 목적", "임직원은 업무상 취득한 고객 정보와 문서를 외부에 유출하지 않는다. 비밀번호는 60일마다 변경한다.",
                      "## 문서 반출", "보안등급 2 이상 문서는 승인 없이 사외로 반출할 수 없으며 외부 공유 링크는 유효기간을 7일 이내로 한다.", "## 신고", "사고는 24시간 이내에 보안팀에 신고한다."), "비밀번호 변경 주기 단축, 신고 조항 신설")
        d.add_comment(U["kim"], sec["id"], "신고 조항이 추가되었네요. @park 경영지원팀 공지도 부탁드립니다.")
        leave = d.register(U["admin"], "연차규정.md", md("연차·휴가 규정", "연차는 입사일 기준으로 부여하며 미사용 연차는 다음 해 3월까지 사용할 수 있다.", "경조사 휴가는 별도 신청서를 제출한다."),
                           folder_id=fid("규정"), doc_type="GEN", tags=["규정", "인사"], expires_at=day(20), description="올해 말 개정 예정 — 만료 예정 알림 예시")
        d.register(U["admin"], "복지안내_2024.md", md("2024 임직원 복지 안내", "2024년 한시 복지 제도 안내. 올해 말 종료되었으며 최신 내용은 연차규정을 따른다. 이 본문은 만료 후 검색·AI 답변에서 제외된다."),
                   folder_id=fid("규정"), doc_type="GEN", tags=["복지"], expires_at="2025-01-01")
        # ---- MICE유치팀 ----
        plan = d.register(U["kim"], "유치계획.md", md("2026 유치 계획", "## 목표", "2026년 신규 계약 12건, 계약금액 합계 30억원을 목표로 한다. 제주관광공사 MICE 위크 후속 사업과 제주해양포럼 전시홀 추가 대관을 핵심 과제로 한다.", "## 일정", "분기별 점검 회의를 개최한다."),
                          folder_id=fid("MICE유치팀"), doc_type="GEN", tags=["유치", "계획"], description="MICE유치팀 연간 계획")
        d.new_version(U["kim"], plan["id"], "유치계획.md", md("2026 유치 계획", "## 목표", "2026년 신규 계약 14건, 계약금액 합계 35억원을 목표로 한다. 제주관광공사 MICE 위크 후속 사업과 제주해양포럼 전시홀 추가 대관, 제주도청 MICE 클러스터 조성을 핵심 과제로 한다.", "## 일정", "분기별 점검 회의를 개최한다."), "목표 상향")
        prop = d.register(U["kim"], "제주관광공사_제안서.md", md("제주관광공사 MICE 위크 2단계 제안서", "EVT-2024 후속으로 MICE 위크 2단계 운영을 제안한다. 예상 계약금액은 3억 2천만원이며 납기는 2026년 9월 30일이다.", "## 범위", "전시·회의 공간 확장 운영, 참가자 만족도 분석 대시보드."),
                          folder_id=fid("MICE유치팀"), doc_type="RPT", tags=["제안", "제주관광공사"], description="결재 대기 예시")
        d.submit(U["kim"], prop["id"], ["park"], "sequential", "금주 내 회신 필요")
        d.add_comment(U["kim"], prop["id"], "@park 단가 검토 부탁드립니다.")
        bad = d.register(U["kim"], "제주도청_견적서.md", md("제주도청 MICE 클러스터 견적서", "CT-2031-003 계약금액은 1억 5천만원이다. 납기는 2026년 11월 30일이다."), folder_id=fid("MICE유치팀"), doc_type="CT", tags=["견적", "제주도청"], description="반려 예시")
        d.submit(U["kim"], bad["id"], ["park"])
        d.reject(U["park"], bad["id"], "계약금액이 계약 DB(1억 2천만원)와 다릅니다. 확인 후 재상신하세요.")
        d.register(U["kim"], "대관계약서_초안.md", md("제주해양포럼 전시홀 추가 대관 계약서(초안)", "전시홀 추가 대관 계약금액은 8천만원이며 대관 기간은 5일이다."), folder_id=fid("MICE유치팀"), doc_type="CT", tags=["계약", "초안"], description="작성 중 예시 — 아직 결재 요청 전")
        # ---- 경영지원팀 ----
        bud = d.register(U["park"], "예산안.md", md("2026 상반기 예산안", "## 요약", "상반기 인건비 12억원, 외주비 7억원, 마케팅비 2억원을 편성한다. 예비비는 총액의 5%로 한다."), folder_id=fid("경영지원팀"), doc_type="RPT", tags=["예산", "재무"], description="경영지원팀 상반기 예산")
        d.submit(U["park"], bud["id"], ["lee"])
        d.approve(U["lee"], bud["id"], "예산 편성 승인")
        d.add_comment(U["park"], bud["id"], "승인 완료되었습니다. 감사합니다.")
        # ---- 인사팀 ----
        d.register(U["lee"], "채용계획.md", md("2026 채용 계획", "상반기 행사운영 5명, MICE 기획 3명 채용을 계획한다. 급여 수준은 인사위원회에서 별도 결정한다."), folder_id=fid("인사팀"), doc_type="GEN", tags=["채용"])
        # ---- legal hold example ----
        disp = d.register(U["kim"], "분쟁관련계약.md", md("분쟁 관련 계약 자료", "제주관광공사 납기 지연 관련 협의 기록. 법무 검토 중이므로 삭제·만료를 금지한다."), folder_id=fid("MICE유치팀"), doc_type="MN", tags=["법무"])
        d.set_hold(U["admin"], disp["id"], True, "법무 검토")
        d.set_favorite(U["kim"], plan["id"], True)
        d.set_favorite(U["kim"], sec["id"], True)
    d.maintenance()
    return True
