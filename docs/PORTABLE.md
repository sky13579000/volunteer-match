# 다른 PC에서 실행하기

필요한 것은 **Python 3.10 이상** 하나뿐입니다 (추가 설치 패키지 없음). 인터넷이 없는 PC도 됩니다.
Windows는 `winget install Python.Python.3.12`, macOS는 `brew install python`, Ubuntu는 `sudo apt install python3`.

## 방법 A — 파일 하나 (`edm-ai.pyz`) ★ 가장 간단
```
python edm-ai.pyz demo        # Windows: py edm-ai.pyz demo
```
- `edm-ai.pyz` 한 개만 USB·메일·공유폴더로 옮기면 됩니다. git/폴더 구조 필요 없음.
- 처음 실행 때 `~/.edm-ai/<버전>/` (Windows: `C:\Users\<이름>\.edm-ai\`) 에 풀리고, 데모 데이터와 작업 공간도 거기에 저장됩니다. 위치는 `EDM_AI_HOME` 환경변수로 바꿀 수 있습니다.
- 만들기: 소스 폴더에서 `python tools/make_pyz.py` → `dist/edm-ai.pyz` (GitHub Actions `ci` 의 결과물 `edm-ai-pyz` 로도 받을 수 있음).
- 새 버전을 받았는데 옛 화면이 보이면: `edm-ai.pyz demo --reset`.

## 방법 B — 폴더(ZIP) 통째로
GitHub 의 ZIP 을 풀고 `run-demo.bat` (Windows) / `./run-demo.sh` (macOS·Linux) 를 실행합니다.
Python 이 없거나 3.10 미만이면 설치 안내가 나옵니다.

## 같은 네트워크의 다른 PC에서 접속하기
```
python edm-ai.pyz demo --lan     # 또는  run-demo.bat --lan
```
화면에 `http://<이 PC의 IP>:<포트>/` 가 표시됩니다. 다른 PC 브라우저에서 그 주소를 열면 됩니다. (Windows 방화벽이 물으면 "허용".)
**데모 로그인은 비밀번호가 없습니다.** 신뢰할 수 있는 내부망 시연용이며, 실제 운영은 `docs/OPERATIONS.md` 의 토큰/SSO 인증과 HTTPS 프록시를 사용하세요.

## 방법 C — Docker
`deploy/docker-compose.yml` 참고 (PostgreSQL 포함 운영형 구성, 폐쇄망용 이미지 빌드 방법은 `docs/OPERATIONS.md`).

## 문제 해결
| 증상 | 조치 |
|---|---|
| `python` 이 없다고 나옴 | Windows 는 `py`, macOS/Linux 는 `python3` 로 실행 |
| 옛 화면/옛 데이터 | `demo --reset` (작업 공간을 새로 만듦) |
| 포트 사용 중 | 자동으로 다음 빈 포트를 쓰며 화면에 주소가 표시됨. `--port` 로 지정도 가능 |
| 한글이 깨짐 | Windows 터미널은 자동으로 UTF-8 설정. 그래도 깨지면 Windows Terminal 사용 |

## 서버 PC 자동 실행 (Windows)
- `start-server.bat` 더블클릭: 이전에 떠 있던 EDM-AI 종료 → LAN 공개로 시작 → `http://<IP>:9100/` 표시. 데이터는 유지됩니다(`start-server.bat --reset` 으로 초기화).
- `install-autostart.bat`: Windows 로그인 때마다 자동 시작 등록. 해제는 `uninstall-autostart.bat`.
- 방화벽: 관리자 권한으로 `start-server.bat` 를 한 번 실행하면 9100~9119 포트 허용 규칙이 등록됩니다.
- IP 가 바뀌지 않게 서버 PC 는 고정 IP(또는 DHCP 예약)를 권장합니다.
