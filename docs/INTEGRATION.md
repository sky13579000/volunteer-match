# 그룹웨어 · SSO · 조직도 연동

세 가지를 독립적으로 쓸 수 있습니다. **구현·테스트 완료**(`tests/test_integration.py`, 29건)와 **환경에서 직접 확인해야 하는 부분**을 구분해 적었습니다.

```
 그룹웨어/HR ──(조직도 CSV·LDIF)──▶ edm-ai sync-users ──▶ users.json ──(자동 반영)──▶ 서버
 사용자 브라우저 ──▶ SSO 프록시(OIDC) ──X-Forwarded-Email──▶ EDM-AI (auth.mode=proxy)
 그룹웨어 결재완료 ──(서명 JSON)──▶ /api/adapters/groupware ──▶ 기안자 권한으로 문서 등록
```

## 1. 조직도 동기화 (`sync-users`)
HR/그룹웨어/AD에서 CSV 또는 LDIF로 내보낸 파일로 `users.json`을 만들고 갱신합니다. 추가 패키지 없음.

```bash
edm-ai -c config.json sync-users 조직도.csv --map examples/directory/map.sample.json            # 미리보기(기본, 아무것도 쓰지 않음)
edm-ai -c config.json sync-users 조직도.csv --map map.json --apply                              # 적용 (users.json.bak 보관)
edm-ai -c config.json sync-users users.ldif --map map.json --apply --prune                      # LDIF, 원본에 없는 사용자 제거
ldapsearch -LLL -x -H ldaps://ad.corp -D "svc" -W -b "dc=corp" "(objectClass=person)" uid cn mail title departmentNumber memberOf > users.ldif
```
- 서버는 `users.json` 변경을 **최대 5초 안에 자동 반영**합니다(재시작 불필요). 깨진 파일이면 이전 목록을 유지하고 로그를 남깁니다.
- 매핑 파일(`map.json`): 열/속성 이름(`fields`), 부서 매핑(`dept_map`), 그룹→역할(`group_roles`), 직급→보안등급(`title_levels`), 등급 상한(`max_level`), 보호 계정(`preserve`), 퇴직자 제외(`ignore_inactive`). 예: `examples/directory/`.
- **안전장치**: ① 기본은 미리보기 ② `admin` 역할은 동기화로 부여·변경·삭제되지 않음 ③ 매핑에 없는 부서는 권한 없는 `external`로 ④ 등급은 `max_level`로 제한 ⑤ 관리자가 없는 결과·이메일 중복은 거부 ⑥ 한 줄씩 경고 출력 ⑦ CSV는 UTF-8/CP949(한국어 Excel) 모두 지원. 슬랙 ID 같이 원본에 없는 `external` 값은 보존됩니다.
- 정기 실행: 작업 스케줄러/cron 에서 `sync-users ... --apply` (예: 매일 새벽).
- **미구현**: LDAP 서버에 직접 접속하는 모드(ldapsearch 결과 LDIF를 사용). 퇴직 즉시 차단이 필요하면 SSO 쪽 계정 비활성화와 함께 쓰세요.

## 2. SSO (OIDC/SAML) — 인증 프록시 방식
EDM-AI 가 직접 OIDC/SAML 을 처리하지 않고, 앞단 프록시가 로그인시킨 뒤 이메일 헤더를 전달합니다. 비밀번호·MFA·세션은 기관의 SSO 가 담당합니다.

```json
"auth": {"mode": "proxy", "header": "X-Forwarded-Email", "match": "email",
         "trusted_proxies": ["172.20.0.5"], "logout_url": "/oauth2/sign_out"}
```
- 로그인 화면이 없어지고 SSO 로 바로 진입합니다. **`users.json` 에 이메일이 없으면 401**(화면에 "등록되지 않았습니다" 안내). 이메일은 대소문자 무시.
- `trusted_proxies`: 이 주소가 아닌 곳에서 온 헤더는 **무시**합니다(헤더 위조 방지). 지정하지 않으면 경고가 로그에 남습니다. EDM-AI 포트는 **프록시만 접근**하도록 네트워크를 분리하세요.
- 예시: `deploy/sso/docker-compose.yml` (oauth2-proxy + OIDC). Keycloak/Entra/Google 모두 OIDC 로 연결됩니다. SAML/AD FS 는 Keycloak 등을 중간에 두는 방식을 권장합니다. **이 compose 는 개발 환경에서 실행해 보지 못했습니다** — 사내 IdP 로 시험 후 쓰세요.

## 3. 그룹웨어 결재 완료 문서 → EDM 등록
그룹웨어(또는 ESB/RPA)가 결재 완료 시 서명된 JSON 을 보냅니다.

설정(`config.json`) 및 비밀값(환경변수, 16자 이상):
```json
"adapters": {"groupware": {"secret_env": "EDM_AI_GW_SECRET", "system": "gw", "max_mb": 50, "trust_approval": true,
  "routes": [{"form": "계약품의", "folder": "MICE유치팀", "doc_type": "CT", "tags": ["그룹웨어"]}],
  "default_route": {"folder": "경영지원팀", "doc_type": "GEN"}}}
```
호출:
```bash
BODY='{"doc_id":"APPR-2026-00123","form":"계약품의","title":"대관 계약 품의","owner":{"type":"email","id":"kim@iccjeju.example"},
 "files":[{"filename":"품의서.pdf","content_b64":"<base64>"}],"approved":true,
 "approval_line":[{"name":"박재정","title":"팀장","result":"승인","at":"2026-10-01 10:00"}]}'
TS=$(date +%s); SIG="sha256=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$EDM_AI_GW_SECRET" -hex | sed 's/^.* //')"
curl -s https://edm.example/api/adapters/groupware -H "X-EDM-Timestamp: $TS" -H "X-EDM-Signature: $SIG" -d "$BODY"
```
- **기안자 권한으로 등록**: `owner` 가 `users.json` 의 이메일(또는 `external`)과 매핑되어야 하고, 폴더 권한·보안등급·감사가 사람이 올릴 때와 동일하게 적용됩니다. 권한 없는 폴더면 해당 건이 `error`(403/404)로 응답됩니다(207).
- **멱등**: 같은 `doc_id` 를 다시 보내면 중복 생성하지 않습니다. 내용이 같으면 `unchanged`, 다르면 새 버전(`updated`). 파일이 여러 개면 모두 등록하고 서로 *관련 문서*로 연결합니다.
- `approved=true` 이고 `trust_approval` 이면 해당 버전이 **즉시 유효(승인) 버전**이 됩니다(결재선은 속성·댓글로 기록). 아니면 초안.
- 양식(`form`) → 폴더·문서유형은 `routes` 로 매핑. 폴더는 **자동 생성하지 않습니다**(없으면 422).
- 보안: HMAC-SHA256 서명 + 5분 유효 + 동일 서명 재전송 거부(409) + 분당 호출 제한 + 본문 크기 제한(`max_mb`).
- 응답: `{"ok":true,"results":[{"index":0,"status":"created|updated|unchanged|error","id":..,"doc_no":..}]}` (일부 실패 시 207).
- **미구현**: 특정 그룹웨어 제품(더존/한컴/다우 등)용 전용 커넥터. 그룹웨어의 "결재 완료 후 외부 URL 호출"(또는 ESB)로 위 JSON 을 만들어 보내도록 설정/개발해야 합니다.

## 4. 더존 BIZBOX ALPHA 와 연동하기
> **솔직한 현황**: BIZBOX ALPHA 의 외부 연동 사양(API·웹훅·SSO·문서 연계)은 공개 자료에서 확인하지 못했습니다. 그래서 *특정 API 를 가정한 코드는 넣지 않았고*, 더존/파트너가 제공하는 어떤 방식이든 붙일 수 있게 **받는 쪽을 범용으로** 만들었습니다. 실제 방식은 아래 질문을 더존(또는 구축 파트너)에 확인해 결정하세요.

**더존/파트너에 확인할 것**
1. 전자결재 **최종 승인 시점에 외부 URL 을 호출**(웹훅/연계 모듈)할 수 있는가? 보낼 수 있는 JSON/XML 항목(문서번호, 양식명, 제목, 기안자 사번/이메일, 상태, 결재선, 첨부)은?
2. 첨부파일을 본문에 실어 보내는가, 다운로드 URL(인증 필요)을 주는가? 최대 크기는?
3. 요청에 **HMAC 서명 헤더**를 붙일 수 있는가? (불가하면 아래 *중계 방식*)
4. 조직도·사용자를 **엑셀/CSV 로 내보내기** 또는 DB 뷰로 제공받을 수 있는가? 사번/이메일 컬럼명은?
5. **SSO**: BIZBOX ALPHA 가 IdP 역할(OIDC/SAML)을 지원하는가, 아니면 AD/LDAP 연동인가?
6. 온프레미스인가 클라우드인가? 서버 간 통신(방화벽) 허용 범위는?

**연동 방식(상황별)**
| 상황 | 방법 |
|---|---|
| 결재 완료 시 외부 URL 호출 가능 + JSON 항목 제공 | `adapters.groupware.field_map` 으로 *그들의 항목명*을 매핑 (코드 변경 없음) |
| 호출은 되지만 서명 불가 | 같은 망에 **작은 중계 서버**(`examples/groupware/send_example.py` 의 `send()` 사용)를 두고, 더존 → 중계(내부 IP 제한) → 서명해서 플랫폼 호출 |
| 호출 불가, 파일/DB 만 제공 | 정해진 시각에 폴더로 내려받은 파일을 **일괄 등록**(`/api/dms/import-zip`) 또는 폴더 수집(`docs.root`)으로 가져오기 |
| 사용자 정보 | 엑셀/CSV → `sync-users` (`external_fields` 로 사번 등 추가 식별자 연결) |
| 로그인 | OIDC/SAML 지원 시 `proxy` 모드, 미지원이면 AD/LDAP 기반 SSO 프록시 또는 플랫폼 자체 토큰 |

**`field_map` 예시** (더존이 아래와 비슷한 JSON 을 보낸다고 가정한 *예시*이며 실제 항목명은 확인 후 바꾸세요)
```json
"groupware": {"secret_env": "EDM_AI_GW_SECRET", "owner_id_type": "empno",
  "field_map": {"doc_id": "approval.docNo", "form": "approval.formName", "title": "approval.subject", "owner_id": "drafter.empNo",
                "approved": {"path": "approval.status", "equals": ["완료"]}, "approval_line": "approval.line",
                "files": {"path": "attachments", "filename": "fileName", "content_b64": "fileData"}},
  "routes": [{"form": "계약품의", "folder": "MICE유치팀", "doc_type": "CT"}]}
```
사번으로 기안자를 찾으려면 `sync-users` 매핑에 `"external_fields": {"empno": "사번"}` 를 두세요. 단점/주의: 첨부가 base64 로 안 오는 경우(URL 방식)는 현재 직접 내려받지 않으므로 중계 서버에서 받아 넣어 주어야 합니다.

## 5. API 키와 송신 웹훅 (v0.6)
**API 키** — 관리자 화면 *연동 관리*에서 발급. 사용자 한 명의 권한으로 동작하며 `read`(GET만) / `write` 범위, 유효기간(최대 365일), 즉시 폐기를 지원합니다. 키는 해시만 저장하고 발급 시 한 번만 보여 줍니다. **관리 API(`/api/dms/admin/*`, 감사)에는 관리자 계정의 키라도 접근할 수 없습니다.** 사용: `Authorization: Bearer edmk_...` (`edm_ai.client.EdmClient(url, token=키)`).

**송신 웹훅** — 플랫폼에서 일어난 일을 다른 시스템에 알립니다(예: 승인 완료 → 그룹웨어/ERP).
```json
"events": {"include_title": false, "webhooks": [
  {"name": "groupware", "url": "https://gw.example/edm-hook", "secret_env": "EDM_AI_HOOK_GW", "events": ["doc.approved", "doc.rejected", "doc.created"]}]}
```
이벤트: `doc.created, doc.version_added, doc.approved, doc.rejected, doc.deleted, doc.expired, doc.acl_changed, doc.moved, doc.legal_hold, doc.legal_hold_released, approval.requested, access.requested, access.granted, privacy.detected, disposal.completed, ping`.
전송은 JSON POST이며 헤더 `X-EDM-Timestamp`, `X-EDM-Signature`(= `sha256=HMAC(비밀값, 타임스탬프 + "." + 본문)`), `X-EDM-Event`, `X-EDM-Delivery`(중복 제거용 ID)를 포함합니다. 백그라운드로 최대 3회 재시도(4xx는 재시도 안 함)하며 요청 처리를 지연시키지 않습니다. 기본은 문서 제목을 보내지 않습니다(`include_title`). 비밀값이 16자 미만이면 해당 웹훅은 비활성화됩니다.

## 점검 체크리스트
1. 조직도 동기화 미리보기 → 경고(미매핑 부서/등급 상한) 해소 → `--apply`
2. SSO: 한 명의 이메일로 로그인 → `/api/me` 확인 → 등록 안 된 계정은 401 확인
3. 그룹웨어: 테스트 양식 1건 전송 → `created` → 재전송 `unchanged` → 변경 후 `updated`
4. 감사 로그(`verify-audit`)에 등록 기록이 기안자 이름으로 남았는지 확인
