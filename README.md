# EDM-AI — 문서중앙화(EDM)를 AI 지식 플랫폼으로

문서·이메일·도면(비정형)과 업무 DB(정형)를 **하나의 권한 모델**로 묶어, AI가 **근거를 인용**해 답하게 하는 참조 구현입니다.
**외부 의존성 0 (Python 표준 라이브러리만)** — 온프레미스·폐쇄망에 그대로 반입할 수 있습니다.

## 빠른 시작
```bash
python3 examples/demo/build_demo.py                      # 데모 ERP DB 생성 + 문서 수집
python3 -m edm_ai.cli -c examples/demo/config.json ask -u kim  "한빛전자 MES 계약 금액과 납기 지연 이슈는?"
python3 -m edm_ai.cli -c examples/demo/config.json ask -u park "한빛전자 MES 계약 금액과 납기 지연 이슈는?"   # 재무: 마진율·원가문서·청구까지
python3 -m edm_ai.cli -c examples/demo/config.json serve --demo-login   # http://127.0.0.1:8080
python3 -m unittest discover -s tests
```
같은 질문도 사용자(`kim` 영업 / `park` 재무 / `lee` 인사 / `choi` 외부)에 따라 **보이는 문서·행·컬럼이 달라집니다.**

## 핵심 설계
| 세미나 주제 | 구현 |
|---|---|
| 정형·비정형 연결 | `entities.py`가 문서에서 프로젝트코드·계약번호·고객사명을 추출 → 최상위 문서의 엔티티로 DB 조회(문서→DB hop) |
| 권한 관리 | 문서 ACL(역할/부서/사용자/거부/보안등급) + DB 컬럼 마스킹 + 행 정책. **점수 계산 전에** 권한 필터링(security trimming) |
| 근거 기반 활용 | 모든 답변 문장에 `[E#]` 인용, 출처(`문서#청크` / `테이블:키`) 반환. 근거 없으면 **거절**, 환각 인용은 검증 후 추출형 답변으로 대체 |
| 온프레미스·폐쇄망 | 표준 라이브러리, SQLite, 로컬 OpenAI 호환 LLM(vLLM/Ollama) 옵션. 외부 호출 없음 |
| 오픈소스·개방형 표준 | md/txt/csv/eml/html 수집, SQL(DB-API), JSON 설정, OpenAI 호환 API, HTTP/JSON |
| 감사 | 질문·사용자·근거 참조(내용 제외)를 `audit`에 기록, `/api/audit`은 admin 전용 |

구조와 보안 모델 상세: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 기존 시스템 연결 방법
1. `config.json` → `docs.root`에 EDM 파일 경로(또는 내보내기 폴더), `manifest.json`에 경로 패턴별 ACL(첫 규칙 우선, 미지정은 `default`).
2. `structured.tables`에 허용할 테이블/컬럼, `link`(엔티티↔컬럼), `columns.*.roles/min_level`(마스킹), `row_policy`(행 정책) 선언. 시작 시 실제 스키마로 검증합니다.
3. `entities.patterns` 정규식과 `dictionaries`(DB 컬럼에서 이름 사전 자동 구축) 정의.
4. LLM 사용 시 `"llm": {"base_url": "http://gpu-host:8000/v1", "model": "..."}`. 미설정이면 LLM 없이 추출형 답변.
5. `ingest`는 증분 수집입니다. 내용 또는 ACL이 바뀐 문서만 재처리하고 삭제된 문서는 제거합니다.

## 한계 (프로토타입)
- 검색은 BM25 + 한글 bi-gram. 의미 검색(임베딩)은 `Index.search` 자리에 로컬 임베딩 모델로 교체/병행 가능.
- DB는 엔티티 키 기반 조회만 지원합니다(자유 SQL/Text-to-SQL 없음 — 의도된 안전 설계).
- 인증은 SSO/프록시 헤더에 위임(`auth_header`). 프로덕션에서는 프록시 뒤에서만 노출하고 `--demo-login`을 쓰지 마세요.
- PDF/Office/도면(DWG) 변환기와 OCR은 `ingest.read_file`에 추가하는 구조입니다.
- 문서 내 프롬프트 인젝션은 "증거=데이터" 프롬프트와 인용 검증으로 완화할 뿐 완전 차단은 아닙니다. 권한 경계는 LLM이 아니라 검색·게이트웨이 계층에서 강제합니다.
