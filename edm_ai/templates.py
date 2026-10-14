"""Document templates (양식). Variables use {{name}}; {{date}} {{year}} {{author}} {{dept}} {{title}} are filled automatically.
The built-in set targets a convention-centre operator; `templates` in config.json adds or replaces entries by id."""

BUILTIN = [
    {"id": "rental-contract", "name": "시설 대관 계약서", "doc_type": "CT", "tags": ["계약", "대관"], "description": "행사 주최 측과 체결하는 시설 대관·운영 계약",
     "fields": [{"key": "organizer", "label": "주최자(계약 상대방)", "placeholder": "제주관광공사"}, {"key": "event", "label": "행사명", "placeholder": "2026 제주 MICE 위크"},
                {"key": "amount", "label": "계약금액", "placeholder": "4억 8천만원"}, {"key": "period", "label": "사용 기간", "placeholder": "2026-11-10 ~ 2026-11-14"},
                {"key": "halls", "label": "사용 시설", "placeholder": "탐라홀, 전시장 A"}],
     "body": "# {{title}}\n\n## 계약 개요\n- 계약 상대방: {{organizer}}\n- 행사명: {{event}}\n- 사용 기간: {{period}}\n- 사용 시설: {{halls}}\n- 계약금액: {{amount}} (부가세 별도)\n\n## 납부\n계약금은 승인 후 14일 이내, 잔금은 사용 30일 전까지 납부한다.\n\n## 취소와 위약금\n시설 사용 규정의 취소 기준을 따른다.\n\n## 원상복구와 지체상금\n철거와 원상복구는 사용 종료 후 24시간 이내에 완료한다. 지연 시 하루당 계약금액의 0.1%를 지체상금으로 부과한다.\n\n작성일: {{date}} / 작성자: {{author}} ({{dept}})\n"},
    {"id": "meeting-minutes", "name": "회의록", "doc_type": "MN", "tags": ["회의록"], "description": "정기·임시 회의 결과 기록",
     "fields": [{"key": "when", "label": "일시", "placeholder": "2026-10-14 10:00"}, {"key": "attendees", "label": "참석자", "placeholder": "김유치, 박재정"},
                {"key": "agenda", "label": "안건", "placeholder": "행사 준비 현황"}],
     "body": "# {{title}}\n\n- 일시: {{when}}\n- 참석자: {{attendees}}\n- 작성자: {{author}}\n\n## 안건\n{{agenda}}\n\n## 논의 내용\n\n\n## 결정 사항\n\n\n## 후속 조치\n| 담당 | 내용 | 기한 |\n|---|---|---|\n|  |  |  |\n"},
    {"id": "event-report", "name": "행사 결과 보고서", "doc_type": "RPT", "tags": ["보고서", "행사"], "description": "행사 종료 후 실적·정산·개선사항 보고",
     "fields": [{"key": "event", "label": "행사명", "placeholder": "2026 제주 MICE 위크"}, {"key": "visitors", "label": "참가자 수", "placeholder": "3,200명"},
                {"key": "revenue", "label": "대관·부대 수입", "placeholder": "4억 8천만원"}],
     "body": "# {{title}}\n\n## 행사 개요\n- 행사명: {{event}}\n- 작성일: {{date}}\n- 작성자: {{author}} ({{dept}})\n\n## 실적\n- 참가자 수: {{visitors}}\n- 수입: {{revenue}}\n\n## 안전·민원 사항\n\n\n## 개선 과제\n\n"},
    {"id": "quotation", "name": "견적서", "doc_type": "CT", "tags": ["견적"], "description": "대관·부대시설 이용 견적",
     "fields": [{"key": "customer", "label": "고객사", "placeholder": "제주해양포럼"}, {"key": "amount", "label": "견적금액", "placeholder": "2억 5천만원"},
                {"key": "valid", "label": "유효기간", "placeholder": "발행일로부터 30일"}],
     "body": "# {{title}}\n\n- 수신: {{customer}}\n- 견적금액: {{amount}} (부가세 별도)\n- 유효기간: {{valid}}\n- 발행일: {{date}}\n\n## 내역\n| 항목 | 수량 | 금액 |\n|---|---|---|\n|  |  |  |\n"},
    {"id": "safety-check", "name": "행사 안전 점검표", "doc_type": "RPT", "tags": ["안전", "점검"], "description": "행사 7일 전 안전 점검 체크리스트",
     "fields": [{"key": "event", "label": "행사명", "placeholder": "2026 제주 MICE 위크"}, {"key": "inspector", "label": "점검자", "placeholder": "시설운영팀 홍길동"}],
     "body": "# {{title}}\n\n- 행사: {{event}}\n- 점검일: {{date}}\n- 점검자: {{inspector}}\n\n## 점검 항목\n- [ ] 비상구·피난 동선 확보\n- [ ] 소방설비 작동 확인\n- [ ] 전기·조명 점검\n- [ ] 임시 구조물 하중·고정 확인\n- [ ] 응급 의료 인력·연락망\n\n## 특이사항\n\n"},
    {"id": "official-letter", "name": "공문(기안문)", "doc_type": "GEN", "tags": ["공문"], "description": "대외 발송 또는 내부 시행 공문",
     "fields": [{"key": "to", "label": "수신", "placeholder": "제주특별자치도청 관광정책과"}, {"key": "subject", "label": "제목", "placeholder": "MICE 행사 협조 요청"}],
     "body": "# {{subject}}\n\n수신: {{to}}\n발신: 제주국제컨벤션센터 {{dept}}\n일자: {{date}}\n\n1. 관련: \n2. 위 호와 관련하여 아래와 같이 협조를 요청드립니다.\n\n   - \n\n붙임: 1부. 끝.\n\n기안자: {{author}}\n"},
]
VARS = ("date", "year", "author", "dept", "title")
