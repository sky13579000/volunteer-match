# Windows에서 실행하기

> 개발 환경(Linux)에서 검증했고, Windows에서 직접 실행해 확인하지는 못했습니다. 막히면 화면 메시지를 알려주세요.

## 0. 준비 (한 번만)
1. **Python 3.10 이상**: https://www.python.org/downloads/windows/ 설치. 첫 화면에서 **"Add python.exe to PATH"** 체크.
2. **Git** (소스 받기용, 선택): https://git-scm.com/download/win — 없으면 GitHub에서 ZIP으로 받아도 됩니다.
3. 확인: PowerShell을 열고 `py --version` → `Python 3.x.x` 가 나오면 정상.

## 1. 소스 받기
```powershell
git clone https://github.com/sky13579000/volunteer-match.git
cd volunteer-match
git checkout claude/edm-ai-integration-platform-dzzryy
```
ZIP으로 받았다면 압축을 풀고, 폴더에서 주소창에 `powershell`을 입력해 터미널을 엽니다.

## 2. 데모 실행
```powershell
py -m edm_ai demo
```
브라우저가 `http://127.0.0.1:8787` 을 엽니다. (다른 PC·파일 1개 배포: [PORTABLE.md](PORTABLE.md)) 종료는 터미널에서 `Ctrl+C`. (또는 폴더의 `run-demo.bat` 더블클릭)

옵션: `py -m edm_ai demo --port 8081` (포트 변경), `--no-browser` (브라우저 안 열기)

## 3. 자주 쓰는 명령 (PowerShell)
```powershell
# 테스트
py -m unittest discover -s tests -v

# 권한 누출 평가 (leaks 0 이어야 정상)
py -m edm_ai -c examples/demo/config.json eval examples/demo/golden.json

# 터미널에서 질문 (사용자: kim MICE유치 / park 경영지원 / lee 인사 / choi 외부)
py -m edm_ai -c examples/demo/config.json ask -u kim  "제주관광공사 MICE 위크 계약 금액과 납기 지연 이슈는?"
py -m edm_ai -c examples/demo/config.json ask -u park "제주관광공사 MICE 위크 계약 금액과 납기 지연 이슈는?"
```

## 4. 실제 운영처럼 (토큰 인증)
PowerShell은 환경변수를 `$env:` 로 설정합니다 (현재 창에서만 유효).
```powershell
$env:EDM_AI_SECRET = "16자-이상의-긴-비밀값-입력"
$tok = py -m edm_ai -c examples/demo/config.json token -u kim
# (config 의 "auth": {"mode": "token"} 설정 후)
py -m edm_ai -c examples/demo/config.json serve --port 8080
```
API 호출 예 (PowerShell):
```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8787/api/ask `
  -Headers @{ Authorization = "Bearer $tok" } `
  -Body (@{ question = "제주관광공사 계약 금액" } | ConvertTo-Json) -ContentType "application/json; charset=utf-8"
```

## 5. PostgreSQL / Docker (선택)
Docker Desktop 설치 후:
```powershell
cd deploy
docker compose up -d --build
```
→ `http://localhost:8080`. 중지: `docker compose down`.

## 문제 해결
| 증상 | 조치 |
|---|---|
| `py` 가 인식되지 않음 | Python 재설치(PATH 체크). 또는 `python` 으로 시도 |
| 포트 오류 / `address already in use` | `py -m edm_ai demo --port 8081` |
| 방화벽 알림창 | 로컬(127.0.0.1)만 쓰면 "취소"해도 됩니다 |
| 한글이 깨져 보임 | `chcp 65001` 입력 후 다시 실행 (Windows Terminal 권장) |
| `스크립트 실행 불가` (ps1) | 이 프로젝트는 .ps1 을 쓰지 않습니다. `py -m ...` 또는 `run-demo.bat` 사용 |
