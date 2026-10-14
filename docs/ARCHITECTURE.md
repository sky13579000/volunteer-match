# 아키텍처

```
                 ┌──────────── SSO / 프록시 (인증) ────────────┐
 사용자 ──HTTP──▶ server.py ── Principal(id, roles, dept, level) ─┐
                                                                  ▼
 EDM 폴더/메일 ──ingest.py──▶ DocumentStore(문서+청크+ACL+엔티티)   Engine.ask
 (manifest ACL)   (증분, ACL 변경 전파)        │                      │
                                              ▼                      │
                              Index(BM25, 권한 선필터) ◀─────────────┤ 1 문서 검색
                                              │ 최상위 문서의 엔티티                │
                                              ▼                                    │
 업무 DB ◀── SqlGateway(읽기전용·화이트리스트·마스킹·행정책) ◀───────┘ 2 DB hop
                                              │
                         번호 매긴 근거 E1..En ─▶ LLM(선택, 로컬) 또는 추출형
                                              │      인용 검증 실패 시 추출형 대체
                                              ▼
                                  Answer + 출처, audit 기록
```

## 권한 모델 (fail-closed)
- **문서**: `Acl(roles, depts, users, deny_users, level)`. 지정이 없으면 `level`만 충족하면 열림. `deny_users`가 최우선. manifest에 없는 파일은 `default`(기본 admin·99)로 **닫힘**.
- **검색 전 필터**: 접근 불가 청크는 후보·IDF·길이 통계 어디에도 들어가지 않습니다(점수 유출 방지). 거절 메시지는 "권한 없는 자료가 있음"을 암시하지 않습니다.
- **DB 컬럼**: `roles`/`min_level` 미충족 시 `●●●` 마스킹. 마스킹된 컬럼으로는 **조회 키로도 쓸 수 없습니다**(필터링을 통한 값 추측 차단).
- **DB 행**: `row_policy` = 무조건 허용 역할 OR 파라미터 바인딩된 WHERE(`:uid :dept :level`). 정책이 있는데 적용 규칙이 없으면 0행.
- **SQL 안전**: 사용자/LLM 입력은 SQL에 닿지 않고 값은 항상 바인딩. 테이블/컬럼은 시작 시 스키마로 검증된 설정값만. 연결은 `mode=ro`.

## 근거 기반 답변
1. 근거는 `E#`로 번호화되어 문서(`경로#청크`) 또는 행(`테이블:키`)을 가리킵니다.
2. LLM은 근거 블록만 받고 `[E#]` 인용을 요구받습니다. 인용이 없거나 범위를 벗어나면 폐기하고 추출형 답변으로 대체합니다.
3. 근거가 0건이면 LLM을 호출하지 않고 거절합니다.

## 확장 지점
| 목적 | 위치 |
|---|---|
| PDF/Office/OCR/CAD 변환 | `ingest.read_file` |
| 의미 검색(임베딩) 병행 | `Index.search` (RRF로 BM25와 결합) |
| Oracle/PostgreSQL/Tibero | `SqlGateway.__init__`의 connect, 나머지는 DB-API 동일 |
| LDAP/AD 그룹 → 역할 | `App.users` 로더 |
| 대규모 문서 | `Index`를 OpenSearch/Lucene 등으로 교체, 권한 필터는 쿼리 조건으로 푸시다운 |
