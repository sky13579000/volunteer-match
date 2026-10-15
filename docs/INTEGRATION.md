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

> **화면에서 연결하기 (v0.7)**: 관리 ▸ 저장소 연동 / 메일 연동 화면의 **"연결 추가"** 로 NAS 경로(UNC·드라이브·마운트)·FTP·WebDAV·IMAP 계정을 직접 등록하고 **"연결 테스트"** 로 확인할 수 있습니다. 설정은 `connections.json`(config.json 옆, 파일 권한 600)에 저장되며 비밀번호는 화면·API에 다시 표시되지 않습니다. 비밀번호를 파일에 두고 싶지 않으면 config.json 에서 `user_env`/`password_env`(환경변수)를 쓰세요. 아래 JSON 설명은 같은 항목의 파일 방식입니다.
> 윈도우 NAS 팁: 서버 PC에서 탐색기로 그 경로가 열려야 합니다. 서비스/예약 작업으로 실행한다면 그 계정이 해당 공유에 접근 권한이 있어야 하고, 네트워크 드라이브(Z:)는 로그인 세션에만 연결되므로 UNC 경로(`\\서버\공유`)를 권장합니다.

## 6. NAS · 파일서버 · FTP · WebDAV 연동 (Wrapsody 식 "가상 중앙화")
문서를 **옮기지 않고** 흩어진 파일을 한곳에서 검색·AI·열람하거나(**원본 유지**), 문서함으로 **이관**합니다. 관리자 화면 *저장소 연동(NAS)*에서 상태 확인·미리보기·지금 동기화.

```json
"sources": [
  {"name": "본부NAS", "type": "path", "path": "\\\\nas01\\share\\문서", "mode": "reference",
   "include": ["*.docx", "*.hwp", "*.pdf"], "exclude": ["~$*", "*.tmp"], "max_mb": 100,
   "acl": {"depts": ["mice"], "level": 1}, "acl_rules": [{"glob": "인사/*", "acl": {"roles": ["hr"], "level": 3}}], "schedule_minutes": 60},
  {"name": "이관NAS", "type": "path", "path": "/mnt/nas", "mode": "migrate", "folder": "NAS이관", "owner": "admin", "doc_type": "GEN",
   "folder_entries": [{"type": "dept", "id": "mice", "role": "viewer"}], "on_delete": "flag"},
  {"name": "협력사FTP", "type": "ftp", "host": "ftp.example", "tls": true, "user_env": "FTP_USER", "password_env": "FTP_PASS", "path": "/share"},
  {"name": "공유WebDAV", "type": "webdav", "url": "https://nas.example/webdav/", "user_env": "DAV_USER", "password_env": "DAV_PASS"}]
```
- **경로(path)**: Windows UNC(`\\서버\공유`), 연결된 드라이브, 리눅스 NFS/SMB **마운트 경로**. SMB 프로토콜을 직접 말하지는 않으므로 서버 OS에서 공유를 먼저 연결하세요(읽기 전용 계정 권장).
- **원본 유지(reference)**: `외부 소스/<이름>/…` 아래에 읽기 전용으로 나타나며 문서를 열면 **항상 원본의 현재 파일**입니다. 접근 권한은 `acl`/`acl_rules` 로 지정(**지정하지 않으면 관리자 전용** — 안전 기본값). FTP·WebDAV는 먼저 로컬 캐시로 **증분 복사**(크기·수정시각)한 뒤 색인합니다.
- **이관(migrate)**: 파일을 문서함의 정식 문서로 복사(폴더 구조 유지, 소유자·권한·태그·원본경로 기록). 다시 실행하면 **변경된 파일만 새 버전**, 같은 내용(SHA-256)은 중복으로 건너뜁니다. 원본에서 사라진 파일은 `원본 삭제됨` 표시만 하고 문서는 지우지 않습니다. 파일별 결과(오류·용량 초과·차단 형식)가 기록됩니다.
- **안전장치**: 서버가 꺼져 있거나 접속 실패 시 **색인을 비우지 않고** 마지막 정상 복사본을 계속 사용, 원격 파일명의 `..`·드라이브 문자 제거, 파일 크기·개수·시간 제한, 실행 파일 차단, `~$*`·임시 파일 제외, 개인정보 차단 정책(`dlp.mode=block`) 적용.
- **PC 동기화 에이전트** `tools/edm_sync.py`: 직원 PC의 폴더를 API 키(write)로 주기 업로드(`--interval`) — 변경분만 전송, 삭제는 하지 않음, `--dry-run` 지원.
- **미구현/미시험**: SMB/NTFS 권한(ACL) 자동 가져오기, SFTP, 클라우드 스토리지(S3·OneDrive). FTP·WebDAV는 시험용 서버로 검증했고 실제 NAS 제품과의 호환은 현장 확인이 필요합니다.

## 7. 이메일 연동 (IMAP 수집 · 링크 발송)

**수집**: `config.json` 의 `mail.imap` 목록에 IMAP 계정을 추가하면, 지정한 메일함의 읽지 않은 메일을 **보낸 사람 이름으로** 문서함에 등록합니다.
보낸 사람의 이메일이 사용자(`users.json` 의 `email`)와 일치해야 하며, `allowed_senders` 로 더 좁힐 수 있습니다.
`register` 는 `message`(본문 .eml) / `attachments`(첨부만) / `both`, 규칙(`rules`)은 제목 `#태그`·`re:` 접두·보낸 사람·받는 사람으로
폴더·문서유형·태그·보안등급·접근권한을 지정합니다. 같은 메일은 Message-ID 로 중복 등록되지 않고, 실행 파일 첨부와 개인정보 차단(DLP block) 정책이 적용됩니다.
처리한 메일은 읽음 표시하거나 `move_to` 로 옮깁니다. 관리 화면 **관리 > 메일 연동** 에서 미리보기(드라이런)·즉시 수집·메일별 결과를 볼 수 있습니다.

**발송**: 문서 상세의 공유에서 이메일을 입력하면 파일을 첨부하지 않고 **받는 사람마다 별도의 만료 링크**를 보냅니다(열람·다운로드가 사람별로 기록, 언제든 회수).
비밀번호는 화면 표시 또는 별도 메일로 보낼 수 있습니다. `notify.smtp` 와 `notify.base_url` 이 필요합니다. API: `POST /api/docs/{id}/send-link`.

## 점검 체크리스트
1. 조직도 동기화 미리보기 → 경고(미매핑 부서/등급 상한) 해소 → `--apply`
2. SSO: 한 명의 이메일로 로그인 → `/api/me` 확인 → 등록 안 된 계정은 401 확인
3. 그룹웨어: 테스트 양식 1건 전송 → `created` → 재전송 `unchanged` → 변경 후 `updated`
4. 감사 로그(`verify-audit`)에 등록 기록이 기안자 이름으로 남았는지 확인

## 8. 최신본 추적 · 열람 현황 (v0.7)

- **사본 확인** (`#/verify`, `POST /api/dms/verify-copy`): 받은 파일 또는 SHA-256 으로 모든 버전과 비교해 *최신본 / 이전 버전 / 수정된 사본 / 없음* 을 알려 줍니다. 열람 권한이 없는 문서는 결과에 나타나지 않습니다.
- **다운로드 표식** (`tracing.tag_downloads: true`): 내려받는 docx·xlsx·pptx·hwpx 는 ZIP 주석, PDF 는 끝부분 주석으로 `EDM-ID:<문서번호>:v<버전>` 을 넣습니다(내용 불변, 텍스트 파일은 변경 안 함). 앱에서 다시 저장하면 표식이 사라질 수 있어 해시 비교가 기본 수단입니다. 내려받은 사본의 해시는 기록되어 표식이 없어도 식별됩니다.
- **항상 최신 링크**: 외부 공유 만들 때 "항상 최신본" 을 선택하면 새 버전이 승인돼도 같은 링크가 최신 내용을 보여줍니다(미선택 시 새 버전 승인 때 링크가 닫힘).
- **열람 현황** 탭(편집 권한자): 사용자별 열람·다운로드 수와 CSV. 같은 사용자 열람은 10분에 1회 기록, `tracing.log_views: false` 로 끌 수 있습니다.
