# LLM · 임베딩 연결과 품질 점검

LLM 없이도 동작합니다(근거 발췌 답변). 모델을 연결하면 요약형 답변과 의미 검색이 켜집니다. **권한은 모델과 무관하게 검색·DB 계층에서 강제**되므로, 모델을 바꿔도 권한 결과는 달라지지 않습니다.

## 1. 모델 서버 준비 (OpenAI 호환이면 무엇이든)
| 서버 | 예 |
|---|---|
| vLLM | `vllm serve <채팅모델> --port 8000` / 임베딩은 별도 인스턴스 `--task embed` |
| Ollama | `ollama pull <채팅모델>` + 임베딩 모델 (`:11434/v1`) — compose 프로파일 `--profile llm` |
| llama.cpp server / TEI | `/v1/chat/completions`, `/v1/embeddings` 제공 |

한국어 임베딩은 다국어 모델(예: bge-m3 계열)을 권장합니다. 폐쇄망이면 모델 파일을 반입해 서버만 내부에 두면 됩니다.

## 2. 설정 (`config.json`)
```json
"llm":      {"base_url": "http://gpu-host:8000/v1", "model": "<채팅모델>", "api_key_env": "LLM_API_KEY", "timeout": 60},
"embedder": {"base_url": "http://gpu-host:8001/v1", "model": "<임베딩모델>"}
```
`api_key_env` 는 환경변수 이름입니다(키를 파일에 쓰지 마세요). 임베딩은 DB에 캐시되고, 서버 장애 시 자동으로 어휘 검색으로 폴백합니다.

## 3. 점검 → 평가
```bash
python3 -m edm_ai -c config.json llm-check      # 연결·지연·인용·숫자 근거·임베딩 품질 (FAIL 이면 종료코드 1)
python3 -m edm_ai -c config.json ingest         # 임베딩 생성(캐시)
python3 -m edm_ai -c config.json eval golden.json
```
`llm-check` 판정: **FAIL** 연결 실패 / `[E#]` 인용 없음 / 근거에 없는 숫자 생성 / 벡터 비정규화 — **WARN** 느림(>10s) / 핵심 사실 누락 / 유의어 유사도가 무관어보다 높지 않음(의미 검색 아님) / `dense_min` 부적절.

`eval` 출력의 `llm_share`(LLM이 답한 비율), `ungrounded_numbers`(근거에 없는 숫자), 골든 케이스의 `must_contain`(답변에 반드시 들어갈 사실)·`must_not_contain`(절대 나오면 안 되는 문구, 위반 시 leak)로 모델 교체 전후를 비교하세요. **`leaks` 는 모델과 상관없이 0 이어야 합니다.**

## 4. 서비스가 모델 답변을 쓰기 전에 하는 검증
1. 근거 0건이면 모델을 호출하지 않고 거절합니다.
2. 문서 속 지시문 의심 줄은 모델 입력에서 무력화합니다.
3. 답변에 `[E#]` 인용이 없거나 범위 밖이면 폐기합니다.
4. 답변의 금액·숫자가 근거에 없으면 폐기합니다(`4억 8천만원` = `480,000,000` 표기 차이는 인정).
5. 폐기·장애·타임아웃이면 근거 발췌 답변으로 대체합니다.

## 5. 임계값 조정
- `dense_min`(기본 0.55): 임베딩 유사도 하한. `llm-check` 가 보여주는 유의어쌍/무관쌍 유사도 사이로 맞추세요.
- `retrieval.min_coverage`(기본 0.2): 어휘 후보가 질문 토큰을 최소 이 비율 덮어야 함.

## 모의 서버 (배선 점검 전용)
```bash
python3 -m edm_ai.mock_llm --port 8001      # 실제 모델이 아님. 설정 예: examples/demo/config.llm-mock.json
```
통신·평가 흐름 확인용입니다. 일부러 핵심 사실을 빼먹는 요약을 하므로 `eval` 이 `MISSING FACT` 로 잡아내는 것이 정상입니다.
