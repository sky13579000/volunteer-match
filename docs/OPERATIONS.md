# 운영 가이드

## 구성 (config.json `dms`)
```json
"dms": {
  "repo": "workspace/repo",            // 저장 파일 위치 (기본: 저장소 DB 옆 repo/). 컨테이너는 /data/repo
  "max_upload_mb": 50, "trash_days": 30, "expiry_warn_days": 30,
  "external_share": {"enabled": false, "max_days": 30, "max_level": 1},   // 기본은 꺼짐 (켤 때 의식적으로)
  "folders": [{"path": "MICE유치팀", "entries": [{"type": "dept", "id": "mice", "role": "editor"}]}],
  "doc_types": [{"code": "CT", "name": "계약서", "pattern": "CT-{year}-{seq:04}", "retention_years": 10, "approval_required": true}]
},
"notify": {"base_url": "https://edm.iccjeju.example",
           "smtp": {"host": "smtp.iccjeju.example", "port": 587, "starttls": true, "from": "edm@iccjeju.example",
                    "user_env": "SMTP_USER", "password_env": "SMTP_PASSWORD", "include_title": true}}
```
- `doc_types`/`folders` 는 **처음 시작할 때만** 반영됩니다. 이후 문서유형은 관리 화면에서, 폴더는 문서함에서 관리하세요.
- 메일 주소는 `users.json` 의 `email`(또는 `external.email`). 어떤 API도 주소를 돌려주지 않습니다. 메일 본문에는 문서/댓글 내용이 없고 문서번호·제목(`include_title:false` 로 끌 수 있음)과 링크만 들어갑니다.
- 업로드 파일은 **저장소 DB 와 repo 폴더가 한 쌍**입니다. 둘을 따로 옮기거나 지우지 마세요.

## 백업 · 복구
```bash
edm-ai -c config.json backup /backup/edm-$(date +%F).zip      # 운영 중 실행 가능 (SQLite 온라인 백업)
edm-ai -c config.json verify-backup /backup/edm-2026-10-14.zip # 해시 매니페스트 검증 → OK / FAILED
edm-ai -c config.json restore /backup/edm-2026-10-14.zip       # 검증 후 복구. 기존 데이터가 있으면 거부
edm-ai -c config.json restore /backup/edm-2026-10-14.zip --force
```
- 포함: 문서 저장소 DB(메타·버전·결재·댓글·감사 로그·번호 채번) + 모든 버전 파일. **제외**: 업무 DB(자체 백업), 수집 대상 원본 폴더, 설정 파일·비밀값.
- 백업 파일에는 **전체 문서가 평문**으로 들어 있습니다. 암호화된 저장소/전송 경로를 쓰고 접근을 제한하세요. 복구 후 감사 로그 해시 체인(`verify-audit`)이 그대로 유지됩니다.
- 복구는 **서버를 멈춘 상태**에서 하세요.

## 정기 작업
서버는 `--reindex-every N` 주기로 (1) 폴더 수집, (2) 유지관리(만료 처리·만료 예정 알림·휴지통 자동 정리)를 실행합니다. 관리 화면 "현황 · 유지관리"에서 즉시 실행할 수 있습니다.

## 보안 점검표
- 운영에서는 `auth.mode=token`(또는 인증 프록시 뒤 header 모드), `--demo-login` 제거, HTTPS 프록시 뒤에서 노출.
- `external_share.enabled` 는 필요할 때만, `max_level` 은 낮게. 링크는 생성 직후 한 번만 보이며 서버엔 해시만 있습니다.
- 감사 로그: 관리 화면에서 무결성 상태 확인, 정기적으로 CSV 보관.
- DB 접속 계정은 SELECT 전용, 서명 비밀값은 환경변수로.
- 다운로드는 항상 첨부(attachment) + `nosniff` + 엄격한 CSP — 업로드된 HTML 이 화면에서 실행되지 않습니다.

## 용량 · 한계
5,000건에서 검색·대시보드 100ms 안팎, 1건 등록 130ms (SQLite, 단일 프로세스). 색인은 메모리에 올라가므로 수만 건 이상은 외부 검색 엔진으로 교체가 필요합니다. 업로드 본문은 JSON(base64)로 전송되어 한 번에 메모리를 크게 쓰므로 `max_upload_mb` 를 환경에 맞게 낮추세요.

## 업그레이드
스키마는 시작 시 자동 마이그레이션됩니다(감사 해시 체인 소급 생성, 폴더 컬럼 추가). **업그레이드 전에 백업**하세요.
