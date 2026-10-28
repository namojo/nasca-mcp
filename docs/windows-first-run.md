# Windows 첫 실행 점검

가장 쉬운 길은 저장소 루트의 `install.ps1`이다. 아래 1~5단계를 대신 해 주고, 끝에 점검까지 돌린다.
이 문서는 스크립트가 막혔을 때 어디를 볼지와, 수동으로 할 때의 순서를 적는다.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -RegisterClaude -Selftest
```

Windows Sandbox 백엔드는 리눅스 개발 환경에서 돌려 볼 수 없어서 첫 실행에서 확인할 것이 있다. 위에서부터 순서대로 한다.

## 1. 사전 조건

- Windows 10/11 Pro, Enterprise, Education. Home은 안 된다.
- "Windows 샌드박스" 기능을 켜고 재부팅. 가상화(BIOS, Hyper-V)가 켜져 있어야 한다.
- 사내 정책이 샌드박스를 막고 있지 않은지 확인한다. 막혀 있다면 이 아이템은 보안팀 협의가 먼저다.
- 파이썬 3.11 이상과 `pip install mcp python-docx openpyxl python-pptx pillow`.

## 2. 런타임 만들기(install.ps1이 이미 했다면 건너뛴다)

```powershell
powershell -ExecutionPolicy Bypass -File runtime\build_runtime.ps1
# 사내 미러를 쓰면: -PipIndex https://사내미러/simple
```

끝에 `런타임 확인 완료`가 나와야 한다. 실패하면 대부분 pip 접속 문제다.

## 3. 점검

```powershell
python -m workbench.doctor
python -m workbench.doctor --selftest
```

`--selftest`는 샌드박스를 실제로 띄워 임시 문서를 열고 읽고 고치고 닫는다. 샌드박스 부팅에 수십 초에서 수 분이 걸린다.

## 4. 막힐 때

| 증상 | 확인 |
| --- | --- |
| 샌드박스가 뜨자마자 꺼짐 | 이미 다른 Windows Sandbox가 실행 중인지. 호스트당 하나만 된다 |
| 5분 안에 준비되지 않음 | 오류 메시지 끝의 게스트 로그 내용. `start.ps1` 실패 이유가 적힌다 |
| 호스트에서 연결이 안 됨 | 게스트 방화벽 규칙의 `-RemoteAddress`가 호스트의 가상 스위치 주소(게스트의 기본 게이트웨이)와 같은지. 게스트에서 `Get-NetRoute`로 확인 |
| 에이전트가 import 오류 | 런타임의 `python\python312._pth`에 `..\site-packages`가 있는지 |
| `python`을 못 찾음 | `.mcp.json`의 command를 `py`나 절대 경로로 바꾼다 |

## 5. NASCA 붙이기

1. NASCA MCP 설정 파일을 만든다(형식은 `src/workbench/nasca/mcp_bridge.py` 머리말).
2. `WORKBENCH_NASCA=mcp`와 `WORKBENCH_NASCA_MCP_CONFIG`를 지정하고 `python -m workbench.doctor --probe-nasca`를 돌린다. 서버가 제공하는 도구 이름과 인자가 나오고, 설정의 `decrypt.tool`, `protect.tool`이 맞는지 알려 준다.
3. 보호 파일 하나로 `python scripts/demo_cycles.py`를 돌리면 암복호화 횟수와 실제 시간이 나온다.
4. 확인할 것: 복호화 응답의 모양(base64인지 파일 경로인지), 보호 저장이 덮어쓰기를 어떻게 다루는지, NASCA 에이전트가 없는 샌드박스 안에서 평문 파일이 정책에 걸리지 않는지.
