# API · 외부 연동

기본 주소 `http://<host>:8080`. 모든 권한 판단은 **서버가 호출자 신원으로** 합니다. 신원은 (1) 서명 토큰, (2) 인증 프록시 헤더, (3) 서명된 어댑터 요청 중 하나로만 정해집니다.

## 인증
| 방식 | 설정 | 사용 |
|---|---|---|
| 토큰 | `"auth": {"mode": "token"}` + `EDM_AI_SECRET`(16자↑) | `Authorization: Bearer $(edm-ai token -u kim)` |
| 프록시 헤더 | `"auth": {"mode": "header"}` | 인증 프록시가 `X-User-Id` 를 주입 (직접 노출 금지) |
| 어댑터 | `adapters.*` + 비밀값 환경변수 | 아래 Slack / 웹훅 (서명 검증) |

## 엔드포인트
오류는 `{"error": "..."}` + 상태코드: 400 형식 · 401 미인증 · 403 권한 없음 · **404 없음(읽을 수 없는 문서도 404)** · 409 충돌/규칙 위반 · 413 크기 · 422 읽을 수 없는 파일 · 429 요청 과다. 본문은 JSON, 파일은 `content_b64`(base64).

### 문서
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/docs?q=&type=&status=&tag=&folder=&owner=&from=&to=&mine=1&favorites=1&origin=&sort=&page=&size=` | 검색·목록. `{total, items[], facets}`; `snippet.hits` 로 하이라이트 위치 제공 |
| POST | `/api/docs` | 등록 `{filename, content_b64, folder_id?, doc_type?, title?, tags?, props?, description?, level?, entries?, inherit?, expires_at?}`. 문서번호 자동 채번, 응답에 `duplicates` |
| GET | `/api/docs/{id}` | 상세: 본문, 권한(`perms`), 버전, 결재 상태, 자동 요약/추출 항목, DB 대조, 연결 DB 행, 관련 문서, 이력 |
| PUT | `/api/docs/{id}` | 정보 수정 `{title, tags, props, description, expires_at, level}` (`props.no_download` = 다운로드 제한) |
| DELETE | `/api/docs/{id}` | 휴지통으로 (보존기간·법적 보류·결재 중이면 409) |
| PUT | `/api/docs/{id}/acl` | `{entries:[{type:user\|dept\|role\|all\|deny, id, role:viewer\|editor\|manager}], inherit, level}` |
| POST | `/api/docs/{id}/move` | `{folder_id}` |
| GET | `/api/docs/{id}/download?ver=` | 다운로드 (항상 attachment, SHA-256 무결성 검증) |
| POST/DELETE | `/api/docs/{id}/links[/{other}]` | 관련 문서 연결(`{other: id 또는 문서번호}`) / 해제 |
| POST | `/api/docs/{id}/favorite` | `{on: true\|false}` |

### 버전 · 결재
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET/POST | `/api/docs/{id}/versions` | 목록 / 새 버전 `{filename, content_b64, comment}` |
| GET | `/api/docs/{id}/diff?a=&b=` | 두 버전의 줄 단위 비교 |
| POST | `/api/docs/{id}/restore` | `{ver}` 이전 버전으로 새 버전 생성 |
| POST | `/api/docs/{id}/checkout` · `/checkout/cancel` | 체크아웃 / 해제 (관리자 강제 해제 가능) |
| POST | `/api/docs/{id}/submit` | 결재 요청 `{approvers[], mode: sequential\|parallel, note}` |
| POST | `/api/docs/{id}/approve` · `/reject` · `/delegate` · `/approval/cancel` | 승인 · 반려(사유 필수) · 위임 `{to}` · 회수 |
| GET | `/api/dms/tasks` | `{tasks: 내가 처리할 결재, requests: 내가 올린 결재}` |

### 수명주기 · 협업
| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/docs/{id}/hold` | 법적 보류 `{hold, reason}` (관리자) |
| POST | `/api/docs/{id}/expiry` | 만료일 변경 `{date}` |
| GET | `/api/dms/trash` · POST `/api/docs/{id}/trash/restore` · DELETE `/api/docs/{id}/purge` | 휴지통 / 복원 / 영구 삭제(관리자) |
| GET/POST/DELETE | `/api/docs/{id}/comments[/{cid}]` | 댓글 `{text, parent_id}`; `@사용자ID` 멘션 |
| GET | `/api/dms/notifications?unread=1` · POST `/api/dms/notifications/read` | 알림함 / 읽음 처리 `{ids?}` |
| GET/PUT | `/api/dms/prefs` | 메일 수신 설정 `{email}` |
| GET/POST/DELETE | `/api/dms/saved[/{id}]` | 저장된 검색 |
| GET | `/api/dms/favorites` · `/api/dms/recent` | 즐겨찾기 / 최근 문서 |

### 폴더 · 일괄 · 공유
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET/POST | `/api/dms/folders` | 폴더 트리(내가 볼 수 있는 폴더만) / 생성 `{parent_id, name, entries}` |
| PUT/DELETE | `/api/dms/folders/{id}` | 이름 `name` · 이동 `parent_id` · 권한 `entries, inherit` 변경 / 삭제(빈 폴더만) |
| POST | `/api/dms/bulk` | `{action: move\|add_tags\|remove_tags\|favorite\|unfavorite\|delete\|set_expiry, ids[], params}` → 건별 결과 |
| POST | `/api/dms/export` | `{ids[]}` → ZIP (다운로드 가능한 문서만, `_manifest.csv`) |
| POST | `/api/dms/import-zip` | `{folder_id, doc_type, content_b64}` 폴더 구조 보존 일괄 등록 |
| GET/POST/DELETE | `/api/docs/{id}/shares[/{sid}]` | 외부 공유 링크 목록 / 생성 `{days, max_downloads, password, perm}` (토큰은 생성 응답에서 한 번만) / 폐지 |
| GET | `/api/dms/dashboard` · `/api/dms/config` · `/api/alerts` | 대시보드 · 설정(유형·사용자·공유 정책) · 문서↔DB 불일치 |

### 관리 (admin)
`GET /api/dms/admin/audit?user=&action=&q=&from=&to=` · `GET /api/dms/admin/audit.csv` · `GET /api/dms/admin/stats` · `POST /api/dms/admin/maintenance` · `PUT /api/dms/admin/doctypes` · `GET /api/audit`(해시 체인 검증)

### 질의응답 · 공개 공유
`POST /api/ask {question}` → 답변 + 근거(`evidence[]`, 문서 근거에는 `doc_id`). 공개: `GET /s/{token}`(페이지), `GET /api/share/{token}`, `POST /api/share/{token}/access|download {password}` — 로그인 불필요, IP별 요청 제한.

## 권한 모델 요약
- **읽기 범위(ACL)** 는 `entries`(폴더 상속 + 문서 직접 지정) + 보안등급 + **생명주기 상태**에서 도출됩니다. 승인 전(작성 중·결재 중·반려)·만료 문서는 소유자·편집 권한자·현재 결재자만 읽습니다. 검색·AI 답변·목록이 모두 같은 함수를 씁니다.
- 역할: 열람(읽기·다운로드·댓글) < 편집(+새 버전·정보 수정·결재 요청) < 관리(+권한·삭제·외부 공유). 소유자는 항상 관리. `deny` 항목은 모든 허용을 이깁니다.
- 관리자(`admin`)는 **읽지 못해도 관리**(권한 변경·삭제·보류·체크아웃 해제)할 수 있습니다.

## Slack 슬래시 명령 (`/edm 질문`)
1. Slack 앱에 슬래시 명령 `/edm` 생성, Request URL = `https://<host>/api/adapters/slack` (HTTPS 프록시 뒤에서).
2. 서버: `EDM_AI_SLACK_SIGNING_SECRET=<Slack Signing Secret>` 설정(16자↑), 설정에 `"adapters": {"slack": {"signing_secret_env": "EDM_AI_SLACK_SIGNING_SECRET"}}`.
3. `users.json` 에 Slack 사용자 ID 매핑: `{"id":"kim", ..., "external": {"slack": "U0123ABCD"}}`.
- 서명(v0) + 5분 타임스탬프 검증. **매핑 없는 Slack 계정은 답변하지 않습니다.** 응답은 `ephemeral`(질문자에게만 보임).

## 서명 웹훅 (그룹웨어·RPA·사내 시스템)
```bash
BODY='{"user":{"type":"email","id":"park@example.co.kr"},"question":"고객사별 계약금액 합계"}'
TS=$(date +%s)
SIG="sha256=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$EDM_AI_WEBHOOK_SECRET" -hex | sed 's/^.* //')"
curl -s http://localhost:8080/api/adapters/webhook \
  -H "X-EDM-Timestamp: $TS" -H "X-EDM-Signature: $SIG" -d "$BODY"
```
서명 = `HMAC-SHA256(secret, timestamp + "." + body)`. 5분 초과·동일 서명 재전송(409)은 거부. 신원은 `users.json` 의 `external` 매핑(`email`, `slack` 등 임의 키)으로만 내부 사용자에 연결됩니다.

## 파이썬 클라이언트
```python
from edm_ai.client import EdmClient
c = EdmClient("http://edm-ai:8080", token="...")
print(c.ask("한빛전자 계약 금액은?")["answer"])
doc = c.upload("계약서.docx", acl={"depts": ["sales"], "level": 2})
print(c.alerts())
```
