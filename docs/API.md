# API · 외부 연동

기본 주소 `http://<host>:8080`. 모든 권한 판단은 **서버가 호출자 신원으로** 합니다. 신원은 (1) 서명 토큰, (2) 인증 프록시 헤더, (3) 서명된 어댑터 요청 중 하나로만 정해집니다.

## 인증
| 방식 | 설정 | 사용 |
|---|---|---|
| 토큰 | `"auth": {"mode": "token"}` + `EDM_AI_SECRET`(16자↑) | `Authorization: Bearer $(edm-ai token -u kim)` |
| 프록시 헤더 | `"auth": {"mode": "header"}` | 인증 프록시가 `X-User-Id` 를 주입 (직접 노출 금지) |
| 어댑터 | `adapters.*` + 비밀값 환경변수 | 아래 Slack / 웹훅 (서명 검증) |

## 엔드포인트
| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/ask` | `{"question": "..."}` → 답변 + 근거(`evidence[]`) |
| GET | `/api/docs?q=` | 내가 읽을 수 있는 문서 목록/검색 (관리자는 읽기 불가 문서도 `readable:false` 로 표시) |
| GET | `/api/docs/{id}` | 상세: 본문, 자동 요약·추출 항목, DB 대조, 연결된 DB 행, 이력 |
| POST | `/api/docs` | 업로드 `{"filename","content_b64","acl"?}` (최대 10MB). `acl` 생략 시 내 부서·보안등급 |
| PUT | `/api/docs/{id}/acl` | `{"acl": {"roles":[],"depts":[],"users":[],"deny_users":[],"level":0}}` 소유자/관리자 |
| DELETE | `/api/docs/{id}` | 업로드 문서 삭제 (소유자/관리자) |
| GET | `/api/alerts` | 문서 ↔ DB 불일치 알림 (내 권한 범위만) |
| GET | `/api/audit` | 감사 로그 + 해시 체인 검증 (admin) |
| POST | `/api/adapters/slack` | Slack 슬래시 명령 |
| POST | `/api/adapters/webhook` | 서명 웹훅 |

오류는 `{"error": "..."}` + 상태코드: 400 형식, 401 미인증, 403 권한 없음, 404 없음(**읽을 수 없는 문서도 404**), 409 충돌, 413 크기, 422 읽을 수 없는 파일, 429 요청 과다.

## 업로드 규칙 (서버 강제)
- 일반 사용자는 **본인 보안등급을 넘는 등급**이나 **본인이 읽을 수 없는 권한**을 지정할 수 없습니다(자기 자신을 제외하는 변경도 거부). 관리자는 가능하며, 읽지 못해도 권한 변경·삭제는 할 수 있습니다.
- 파일명은 경로 문자 제거 후 `uploads/` 아래에만 저장. 지원 형식 외·손상 파일은 거부(저장 안 함).
- UI에서 바꾼 권한은 manifest 규칙보다 우선하며 재수집에도 유지됩니다.

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
