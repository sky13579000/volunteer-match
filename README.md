# EDM-AI — 문서중앙화(EDM)를 AI 지식 플랫폼으로

문서·이메일·도면(비정형)과 업무 DB(정형)를 **하나의 권한 모델**로 묶어, AI가 **근거를 인용**해 답하게 하는 참조 구현입니다.
**외부 의존성 0 (Python 표준 라이브러리만)** — 온프레미스·폐쇄망에 그대로 반입할 수 있습니다.

## 빠른 시작
**가장 빠른 방법 (Python 3.10+만 있으면 됨, 설치할 것 없음):**
```bash
./run-demo.sh            # Windows: run-demo.bat   (또는 python3 -m edm_ai demo)
```
샘플 데이터를 만들고 서버를 띄운 뒤 브라우저를 엽니다. **터미널에 크게 찍히는 주소**(기본 http://127.0.0.1:8787, 사용 중이면 다음 빈 포트)를 여세요.
- 첫 줄에 `EDM-AI v0.4.0`, 브라우저 왼쪽 아래에도 같은 버전이 보여야 최신입니다.
- 이전 버전이 8080에 떠 있으면 새 데모가 **경고를 출력**하고 다른 포트를 씁니다(옛 서버는 `Ctrl+C` 로 끄세요).
- `--reset` 은 데모 데이터를 깨끗이 다시 만들고, `--port N` 은 시작 포트, `--no-browser` 는 브라우저 자동 열기를 끕니다.

**Windows 사용자는 [docs/WINDOWS.md](docs/WINDOWS.md)** 를 보세요(PowerShell 명령어 기준).

개별 명령:
```bash
python3 examples/demo/build_demo.py                      # 데모 ERP DB 생성 + 문서 수집
python3 -m edm_ai.cli -c examples/demo/config.json ask -u kim  "한빛전자 MES 계약 금액과 납기 지연 이슈는?"
python3 -m edm_ai.cli -c examples/demo/config.json ask -u park "한빛전자 MES 계약 금액과 납기 지연 이슈는?"   # 재무: 마진율·원가문서·청구까지
python3 -m edm_ai.cli -c examples/demo/config.json serve --demo-login   # http://127.0.0.1:8787
python3 -m unittest discover -s tests
python3 -m edm_ai.cli -c examples/demo/config.json eval examples/demo/golden.json   # 정확도 + 권한 누출(0이어야 함) 평가
EDM_AI_SECRET=<16자 이상> python3 -m edm_ai.cli -c cfg.json token -u kim            # 서명 토큰 (auth.mode=token)
python3 -m edm_ai.cli -c cfg.json verify-audit                                      # 감사 로그 변조 검증
```
같은 질문도 사용자(`kim` 영업 / `park` 재무 / `lee` 인사 / `choi` 외부)에 따라 **보이는 문서·행·컬럼이 달라집니다.**

## VS Code에서 열기
1. VS Code로 `volunteer-match` 폴더를 열고, 오른쪽 아래 "권장 확장 설치" 알림에서 Python 확장을 설치합니다.
2. **F5** → "EDM-AI 데모 실행" 을 고르면 데모 데이터 생성 + 서버 기동 + 브라우저 열기까지 자동입니다(중단점 디버깅 가능).
3. `Ctrl+Shift+P` → "Tasks: Run Task" 에서 테스트 실행 / 평가 / Docker 스택 기동을 고를 수 있습니다. 왼쪽 비커(테스트) 아이콘에서도 테스트가 보입니다.
4. 설치 없이 쓰려면 **Dev Container / GitHub Codespaces**로 여세요(`.devcontainer`). 포트 8080이 자동 전달되어 브라우저에서 바로 열립니다.
5. Claude Code 확장(`anthropic.claude-code`)을 설치하면 VS Code 안에서 같은 저장소를 두고 Claude와 이어서 작업할 수 있습니다.

## 운영 배포
```bash
# 1) 전체 스택 (PostgreSQL + EDM-AI + 웹 UI), http://localhost:8080
cd deploy && docker compose up -d --build            # 사설 CA 뒤라면: CA_BUNDLE=/path/ca.pem docker compose up -d --build
# 2) 컨테이너 없이 (폐쇄망): 휠 반입 후  pip install --no-index --find-links wheels "edm-ai[postgres,pdf]"  →  deploy/edm-ai.service
```
- 컨테이너는 비루트, 읽기 전용 루트FS, 모든 capability 제거로 실행됩니다. 포트는 `127.0.0.1`에만 바인딩 — 실제 운영에서는 인증 프록시 또는 `auth.mode=token` 뒤에 두세요(`--demo-login` 제거).
- DB 연결: `structured.driver` = `sqlite` | `postgres` | `oracle`. 접속 정보는 **환경변수**(`dsn_env` 등)로만 주입합니다. `deploy/postgres/seed.sql`에 SELECT 전용 계정(`edm_ro`) 예시가 있습니다. 서버 세션도 읽기 전용으로 강제하므로 2중 방어입니다.
- `serve --ingest --reindex-every 300`: 기동 시 + 5분마다 증분 수집(변경·삭제·ACL 변경 반영).
- 웹 UI(`/`): 사용자별 질의응답, `[E#]` 인용 칩 → 근거 카드, 문서/DB/집계 구분, 마스킹 컬럼 표시, 관리자 감사 로그 탭. 엄격한 CSP, 모든 데이터는 `textContent`로만 렌더링.

## 검증 현황
| 항목 | 상태 |
|---|---|
| 단위·통합 테스트 | `python3 -m unittest discover -s tests` |
| PostgreSQL 16 실서버 | ✅ 통합 테스트 + 골든셋(재현율 1.0, 누출 0). `EDM_TEST_PG_DSN` 설정 시 실행 |
| 브라우저 UI (Chromium) | ✅ 로그인/질의/근거/거절/감사/XSS 시나리오 |
| Docker 이미지·compose 스택 | ✅ 빌드·기동·PostgreSQL 연동·권한별 응답 확인 |
| Oracle Free 23ai 실서버 | ✅ 통합 테스트 5건 + 골든셋(SQLite·PostgreSQL과 동일 결과). `ERP_ORA_*` 설정 시 실행 |
| LLM/임베딩 HTTP 통신 | ✅ 스텁·모의 서버로 요청 형식·인증·인용/숫자 검증·폴백·`llm-check`·`eval` 전체 경로 검증. **실제 모델(vLLM/Ollama)의 답변 품질은 미검증** |
| Slack/웹훅 | ✅ 서명 검증(Slack 공식 예시 값 일치)·재전송 방어·미매핑 사용자 거부. 실제 Slack 워크스페이스 연동은 미검증 |
| 브라우저 e2e | ✅ `tests/e2e/ui.e2e.js` 18개 (문서·업로드·권한 변경·알림·XSS) |

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

## v0.4 — 문서중앙화(EDM) 전체 기능
폴더 트리·문서번호 채번·버전/체크아웃/비교·**결재(순차/병렬/위임)**·보존·법적 보류·휴지통·만료·댓글/멘션·알림(+메일)·즐겨찾기·**외부 공유 링크**·ZIP 일괄 등록/내보내기·대시보드·감사/관리 화면·백업/복구까지 구현했습니다. **승인된 문서만 AI 답변에 쓰이고, 읽을 권한이 없는 문서는 모든 화면·API·AI에서 존재 자체가 드러나지 않습니다.**
- 전체 기능표와 한계: [docs/FEATURES.md](docs/FEATURES.md) · 운영: [docs/OPERATIONS.md](docs/OPERATIONS.md) · API: [docs/API.md](docs/API.md)
- 브라우저 e2e: `tests/e2e/ui.e2e.js` (58개) — 신규 작업공간에서 `python3 -m edm_ai demo --no-browser` 로 띄운 뒤 실행

## v0.3 추가 기능 (EDM · 자동화 · 연동)
- **EDM 화면**: 문서 목록·검색, 업로드(권한 지정), 상세(자동 요약/추출 항목/DB 대조/연결 DB 행/이력), 소유자·관리자 권한 편집, 삭제. 읽을 수 없는 문서는 404.
- **업무 자동화**: 문서에서 계약번호·계약금액("4억 8천만원")·납기를 추출해 업무 DB와 **호출자 권한으로** 대조 → 알림 탭/배지. 가려진 컬럼은 대조하지 않음.
- **LLM 점검·평가**: `llm-check`(연결·인용·숫자 근거·임베딩 품질), 답변 숫자 근거 검증(지어낸 금액 폐기), `eval`의 `must_contain`/`llm_share`. [docs/LLM.md](docs/LLM.md)
- **외부 연동**: Slack 슬래시 명령(서명 검증), 서명 웹훅, 파이썬 클라이언트. 자세한 내용은 [docs/API.md](docs/API.md).

## v0.2 추가 기능
- **하이브리드 검색**: BM25 + 벡터 유사도를 RRF로 결합, 둘 다 권한 선필터. 벡터는 기본 해시 트라이그램(오타·띄어쓰기에 강함, 의미 검색 아님) → `"embedder": {"base_url","model"}` 설정 시 로컬 임베딩 서버(bge-m3 등)로 진짜 의미 검색. 임베딩은 DB에 캐시되고, 서버 장애 시 자동 폴백.
- **문서 형식**: docx/xlsx/pptx/odt/ods/odp(표준 라이브러리), PDF(`pdftotext` 또는 `pypdf`가 있을 때). 손상·XXE·zip bomb 파일은 색인하지 않고 `skipped`로 보고.
- **통제된 집계**: `structured.metrics`에 선언한 합계/평균/건수만 허용. 행 정책을 집계 *이전에* 적용하고, 가려진 컬럼이 하나라도 쓰이면 계산하지 않음. 범위는 질문에 명시된 엔티티.
- **보안**: 개인정보(주민번호·전화·카드·이메일) 자동 마스킹(`pii.reveal_roles`만 원문), 문서 내 프롬프트 인젝션 줄 무력화(LLM 입력), HMAC 서명 토큰 인증, 사용자별 요청 제한, 해시 체인 감사 로그(변조 시 `verify-audit` 실패).
- **평가**: 골든셋으로 recall·MRR·**권한 누출 건수**를 측정하는 `eval` 명령 (누출>0이면 종료코드 1 → CI에 연결).

## 기존 시스템 연결 방법
1. `config.json` → `docs.root`에 EDM 파일 경로(또는 내보내기 폴더), `manifest.json`에 경로 패턴별 ACL(첫 규칙 우선, 미지정은 `default`).
2. `structured.tables`에 허용할 테이블/컬럼, `link`(엔티티↔컬럼), `columns.*.roles/min_level`(마스킹), `row_policy`(행 정책) 선언. 시작 시 실제 스키마로 검증합니다.
3. `entities.patterns` 정규식과 `dictionaries`(DB 컬럼에서 이름 사전 자동 구축) 정의.
4. LLM 사용 시 `"llm": {"base_url": "http://gpu-host:8000/v1", "model": "..."}`. 미설정이면 LLM 없이 추출형 답변.
5. `ingest`는 증분 수집입니다. 내용 또는 ACL이 바뀐 문서만 재처리하고 삭제된 문서는 제거합니다.

## 한계 (프로토타입)
- 기본 검색은 어휘 기반(BM25 + 퍼지 벡터)입니다. 동의어·의미 검색은 임베딩 서버를 설정해야 합니다.
- 인메모리 색인이라 수십만 청크 이상은 OpenSearch 등으로 교체가 필요합니다.
- DB는 엔티티 키 기반 조회와 선언된 집계만 지원합니다(자유 SQL/Text-to-SQL 없음 — 의도된 안전 설계).
- Oracle은 따옴표 식별자(대소문자 구분)를 쓰므로 설정의 테이블/컬럼명을 저장된 그대로(보통 대문자) 적어야 합니다(`examples/demo/config.oracle.json`, `deploy/oracle/seed.sql` 참고). 서버 측 읽기 전용은 `SET TRANSACTION READ ONLY`로 강제됩니다.
- 관련도 관문(`retrieval.min_coverage`, 기본 0.2): 질문 토큰의 20% 미만만 겹치는 문서는 근거로 쓰지 않습니다. 코퍼스에 맞춰 `eval`로 조정하세요.
- `auth.mode=header`는 인증 프록시 뒤에서만 쓰세요(클라이언트 헤더를 신뢰). 직접 노출 시 `token` 모드. `--demo-login`은 데모 전용.
- PDF/Office/도면(DWG) 변환기와 OCR은 `ingest.read_file`에 추가하는 구조입니다.
- 문서 내 프롬프트 인젝션은 "증거=데이터" 프롬프트와 인용 검증으로 완화할 뿐 완전 차단은 아닙니다. 권한 경계는 LLM이 아니라 검색·게이트웨이 계층에서 강제합니다.
