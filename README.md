# nasca-mcp

NASCA DRM으로 보호된 문서를 AI가 읽고 고칠 때, 문서를 **한 번만 복호화해 격리된 작업구역(Windows Sandbox) 안에 두고**
그 안에서 읽기와 수정을 반복한 뒤 **한 번만 다시 보호해 내보내는** MCP 서버다.
Claude Code 플러그인으로 설치되고, Codex 같은 다른 MCP 클라이언트에도 붙는다. 플러그인 이름은 `workbench`다.

## 왜 필요한가

NASCA는 PC 트레이의 에이전트가 문서를 열 때 복호화하고 문서 앱이 그 평문을 연다. 그래서 AI는 평문에 닿으려면 앱을 COM으로 긁거나 화면을 캡처해야 한다.
COM은 호출마다 프로세스 간 왕복이 일어나 느리고 앱이 편집 중이면 멈춘다. 캡처는 이미지 토큰이 많이 들고 글자가 깨진다. 수정마다 복호화와 암호화를 되풀이하는 것도 시간을 쓴다.

| | 지금 | nasca-mcp |
| --- | --- | --- |
| 문서 접근 | 앱을 COM으로 조작, 화면 캡처 | 평문 파일을 라이브러리로 직접 읽고 씀(앱 불필요) |
| 암복호화 | 수정마다 1회씩 | 열 때 1회, 내보낼 때 1회 |
| 이미지 | 그림판 복호화 후 캡처 | 파일을 그대로 비전에 전달, 영역 확대 가능 |
| 평문 위치 | PC 곳곳(앱 메모리, 임시 파일) | 세션 하나의 격리 환경 안, 종료하면 삭제 |

## 일반 작업에는 영향이 없다

이 도구는 AI가 쓸 때만 동작하고, 윈도우의 일반 파일 읽기, 쓰기, 수정, 삭제를 바꾸지 않는다.

**시스템에 설치하지 않는 것.** 드라이버, 서비스, 시작 프로그램, 예약 작업, 레지스트리, PATH, 호스트 방화벽, NASCA 설정과 에이전트를 건드리지 않는다.
설치가 만드는 것은 사용자 폴더 아래 두 곳(`%LOCALAPPDATA%\nasca-mcp` 가상환경, `%LOCALAPPDATA%\workbench` 설정과 런타임)뿐이다.
설치 스크립트가 이 약속을 지키는지 자동 시험이 확인한다. Windows 샌드박스 기능을 켜는 것은 `-EnableSandboxFeature`를 줄 때만 한다.
샌드박스 안의 방화벽 잠금은 샌드박스 VM에만 적용되고 종료하면 사라진다.

**상시 실행하는 것이 없다.** MCP 서버는 AI 클라이언트가 띄운 동안만 실행된다. 샌드박스는 첫 `workbench_open`에서 시작해 닫거나 시간이 지나면 꺼진다. 클라이언트가 죽어도 샌드박스는 스스로 평문을 지우고 꺼진다.

**AI의 일반 파일 작업은 그대로다.**
- 접근 통제 훅은 정책(`policy.json`)에 적은 보호 폴더와 차단 MCP 이름에만 반응한다. 정책이 없으면 아무것도 하지 않는다.
- 보호 폴더 밖의 파일은 평소처럼 Read, Edit, Write, Bash로 읽고 쓰고 고치고 지운다.
- 정책 파일이 깨졌거나 훅에 오류가 나도 막지 않는다. 오류는 로그에 남고 세션 시작 때 알려 준다.
- 설치 스크립트는 열 수 있는 폴더를 보호 폴더로 한정한다. 일반 파일은 작업구역을 거치지 않는다.

**작업구역과 호스트 파일이 엇갈리지 않는다.**
- `workbench_*`는 `workbench_open`으로 연 문서와 `workbench_create`로 만든 문서만 다룬다. 호스트 파일은 열 때 읽고, 내보낼 때만 쓴다.
- `workbench_delete`는 작업구역 안의 사본만 지운다. 원본은 지우지 않는다.
- 같은 원본을 두 이름으로 열 수 없다. 사본이 둘로 갈라지는 것을 막는다.
- 원본을 덮어써서 내보낼 때 그 사이 원본이 바뀌었으면 거부한다(`force`로만 덮어쓴다).
- 내보내지 않은 수정이 있는 파일은 닫거나 지울 때 확인을 요구한다.

알려진 한계는 아래 "한계"에 있다.

## 설치(Windows)

요구사항: Windows 10/11 Pro, Enterprise, Education, 파이썬 3.11 이상(PATH에 `python`), Windows 샌드박스 기능, 사내 NASCA MCP(없으면 모의 NASCA로 설치됨).

```powershell
git clone https://github.com/namojo/nasca-mcp
cd nasca-mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1 -RegisterClaude
```

보호 폴더와 NASCA MCP를 지정하는 실제 설치 예:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 `
  -NascaMcpConfig C:\tools\nasca-mcp.json `
  -ProtectedRoot 'D:\NASCA\보안문서' -ExportRoot 'D:\NASCA\결과' `
  -RegisterClaude -Selftest
```

| 옵션 | 뜻 |
| --- | --- |
| `-NascaMcpConfig` | 사내 NASCA MCP 설정 JSON. 없으면 모의 NASCA(보안 기능 없음) |
| `-ProtectedRoot` | AI가 직접 열지 못하게 막을 보호 폴더(여러 개 가능). 열 수 있는 폴더의 기본값도 된다 |
| `-OpenRoot`, `-ExportRoot` | 열 수 있는 폴더, 내보낼 수 있는 폴더. 비우면 제한 없음 |
| `-BlockedMcpServer` | AI가 직접 호출하지 못하게 막을 원본 NASCA MCP 서버 이름(기본 `nasca`) |
| `-RegisterClaude`, `-ClaudeScope` | Claude Code 플러그인 등록, 범위(`user` 기본, `project`, `local`) |
| `-RegisterCodex` | Codex에 MCP 서버로 등록 |
| `-PipIndex`, `-PythonEmbedZip` | 사내 PyPI 미러, 인터넷 없이 쓸 내장 파이썬 zip |
| `-EnableSandboxFeature` | Windows 샌드박스 기능 켜기(관리자, 재부팅 필요) |
| `-SkipRuntime`, `-Selftest`, `-Force` | 런타임 빌드 건너뛰기, 샌드박스 실제 기동 점검, 설정 파일 덮어쓰기 |

설치 스크립트는 여러 번 실행해도 된다. 끝에 `doctor`를 돌려 문제를 알려 준다. 막힐 때의 확인 지점은 [docs/windows-first-run.md](docs/windows-first-run.md).
제거는 `powershell -ExecutionPolicy Bypass -File .\uninstall.ps1`이다. 기본은 가상환경과 등록만 지우고 설정과 감사 로그는 남긴다.

macOS와 리눅스에서는 격리가 없는 개발용 로컬 백엔드만 쓸 수 있다(아래 "개발").

## 클라이언트 연결

**Claude Code**: 설치 스크립트의 `-RegisterClaude`가 아래를 실행한다. 직접 하려면:

```bash
claude plugin marketplace add namojo/nasca-mcp      # 또는 클론한 폴더 경로
claude plugin install workbench@nasca-mcp
```

플러그인은 MCP 서버, 직접 접근 차단 훅, 사용 지침(skill)을 한 번에 들여온다. 도구 이름은 `mcp__plugin_workbench_workbench__workbench_open` 형식이다.
설치 스크립트가 만든 가상환경이 있으면 플러그인이 그 파이썬으로 서버를 띄운다(`scripts/launch_mcp.py`).
GitHub에서 설치한 플러그인은 캐시로 복사되고, 로컬 폴더로 등록하면 그 폴더에서 바로 읽으니 폴더를 지우지 않는다.

**Codex**: `enforcement/codex/config.toml.example`를 `~/.codex/config.toml`에 합치거나 `-RegisterCodex`를 쓴다.
첫 `workbench_open`에 샌드박스 기동 시간이 들어가므로 `tool_timeout_sec = 400` 정도로 둔다(기본 60초).

**그 밖의 MCP 클라이언트**: `<가상환경>\Scripts\python.exe -m workbench.mcp_server`를 stdio로 등록한다.

AI가 이 경로만 쓰게 강제하는 방법과 한계는 [docs/enforcement.md](docs/enforcement.md).

## 사용법

AI에게 평소처럼 말하면 된다. 보호 폴더의 문서를 다루는 요청이면 AI가 작업구역 도구를 고른다.

> D:\NASCA\보안문서\설계서.docx 를 열어서 외부 연동 API 요구사항만 표로 정리해 줘.

> 구성도.png 의 오른쪽 위 영역을 확대해서 어떤 구성요소가 있는지 설명해 줘.

> 설계서에서 REST를 gRPC로 바꾸고 D:\NASCA\결과\설계서_v2.docx 로 저장해 줘.

흐름은 `workbench_open`(복호화, 세션 시작) → `workbench_outline`, `workbench_read`, `workbench_view_image`(읽기) → `workbench_edit`(수정) → `workbench_export`(재보호해 저장) → `workbench_close`(평문 삭제)다.
한 번 연 문서는 닫을 때까지 복호화된 상태로 남으므로 같은 문서를 다시 열 필요가 없다. 첫 `workbench_open`은 샌드박스 부팅 때문에 시간이 걸린다(실측 전).

| 도구 | 하는 일 |
| --- | --- |
| `workbench_open` | 복호화해 작업구역에 넣고 구조(outline)를 돌려준다. 첫 호출이 세션을 시작한다 |
| `workbench_outline`, `workbench_read` | 구조만 보기, 구간만 읽기. 쪽 나눔 안내가 붙어 토큰을 아낀다 |
| `workbench_view_image`, `workbench_extract_media` | 이미지를 비전으로 보기(영역 확대 가능), 문서 안의 그림 꺼내기 |
| `workbench_edit`, `workbench_create`, `workbench_undo`, `workbench_delete` | 수정(전부 적용 또는 전부 취소), 새 문서, 되돌리기, 작업구역 사본 지우기 |
| `workbench_export` | DRM으로 재보호해 호스트 폴더에 저장. 평문이 나가는 유일한 길 |
| `workbench_close`, `workbench_status`, `workbench_list` | 닫기(평문 삭제), 상태와 규칙, 목록 |

지원 형식은 docx, xlsx, pptx, 이미지(png, jpg, gif, bmp, webp, tiff), 텍스트(UTF-8, CP949)다. 그 밖의 형식은 열고 내보내는 것만 된다.
수정 형식(`ops`)은 도구 설명에 있고, 세션 중에는 `workbench_status`가 규칙을 보여 준다.

## 설정

우선순위는 기본값, 사용자 설정(`%LOCALAPPDATA%\workbench\config.json`), 환경변수(`WORKBENCH_<이름>`), 기계 설정(`%ProgramData%\workbench\config.json`) 순이고 뒤가 이긴다.
기계 설정은 관리자만 쓸 수 있는 위치라서, 거기 적은 값은 사용자와 환경변수가 바꾸지 못한다. IT가 열 수 있는 폴더, 백엔드, NASCA 어댑터를 고정하는 용도다.

| 키 | 기본값 | 뜻 |
| --- | --- | --- |
| `backend` | Windows는 `winsandbox`, 그 밖은 `local` | 격리 백엔드. `local`은 격리가 없는 개발용 |
| `nasca` | `mock` | `mcp`면 사내 NASCA MCP를 쓴다 |
| `nasca_mcp_config` | 없음 | NASCA MCP 설정 JSON 경로 |
| `idle_ttl_s`, `max_ttl_s`, `lease_s` | 900, 14400, 120 | 무동작 종료, 최대 수명, 중개 계층 연락 두절 시 자동 삭제까지 |
| `open_roots`, `export_roots` | 제한 없음 | 열 수 있는 폴더, 내보낼 수 있는 폴더 |
| `runtime_dir`, `memory_mb` | 설정 폴더의 runtime, 4096 | 샌드박스 런타임 위치, 샌드박스 메모리 |
| `max_open_bytes`, `max_files` | 200MB, 100 | 열 수 있는 파일 크기와 개수 |

**NASCA MCP 설정.** 사내 NASCA MCP의 도구 이름과 응답 모양은 이 저장소가 알 수 없어 설정 파일로 열어 두었다. 아래는 예시이므로 실제 값으로 고친다.
`python -m workbench.doctor --probe-nasca`가 서버의 도구 이름과 인자를 보여 주고 설정이 맞는지 알려 준다.

```json
{
  "command": "node", "args": ["C:/tools/nasca-mcp/index.js"], "call_timeout_s": 120,
  "decrypt": {
    "tool": "nasca_decrypt", "args": {"path": "{path}"},
    "result": {"mode": "base64", "field": "content_base64"}
  },
  "protect": {
    "tool": "nasca_protect", "input": "base64",
    "args": {"path": "{dest}", "content_base64": "{data_b64}", "overwrite": "{overwrite}"},
    "result": {"path_field": "path"}
  }
}
```

**접근 통제 정책**(`policy.json`)은 `enforcement/policy.example.json`을 복사해 쓴다.

## 검증한 것과 못 한 것

확인했다.
- 자동 시험이 리눅스에서 통과한다. MCP 서버와 NASCA 어댑터는 mcp 2.3과 1.30 양쪽에서 시험했다.
- Claude Code 2.1.312에 플러그인을 켜고 실제 모델로 돌렸다. 열기, 읽기, 수정, 재확인, 내보내기, 닫기가 도구 8턴으로 끝났고, 내보낸 파일은 보호 형식이며 작업 루트는 남지 않았다.
- 같은 환경에서 보호 폴더에 대한 Read, Bash, Grep, Glob이 훅에 막혔고 사유가 모델에게 전달됐다. 보호되지 않은 파일은 읽혔다.
- `claude plugin validate`가 플러그인과 마켓플레이스 매니페스트를 통과시켰고, 격리된 설정 폴더에서 `marketplace add`와 `install`이 동작했다.
- 감사 로그의 해시 체인이 유효하고, 로그에 문서 내용이 없다.

확인하지 못했다. 이 환경이 리눅스라 돌려 볼 수 없었다.
- **Windows Sandbox 백엔드**: `.wsb` 생성, 게스트 방화벽 스크립트, 런타임 빌드는 구조와 문법 수준만 시험했다. 실제 기동은 첫 실행에서 확인해야 한다.
- **PowerShell 스크립트**(`install.ps1`, `uninstall.ps1`, `runtime\*.ps1`): 실행하거나 파서로 검사하지 못했다. 괄호 균형, 인코딩, 알려진 실수 패턴, 시스템 변경 명령 부재를 정적으로 시험했을 뿐이다.
- **실제 NASCA 연동**: 모의 서버로만 시험했다. 샌드박스 안의 평문이 NASCA 정책에 걸리지 않는지도 실제 환경에서 확인해야 한다.
- **Codex 연결**, 관리형 설정 키의 실제 동작, Windows 드라이브 경로를 쓰는 deny 규칙 표기.

## 한계

- 로컬 백엔드는 격리가 없는 개발용이다. 운영은 `winsandbox`다.
- Windows 샌드박스는 호스트당 하나만 실행되므로 세션도 하나다. 다른 클라이언트가 먼저 쓰고 있으면 `workbench_open`이 안내와 함께 실패한다.
- 한글(HWP)과 PDF는 읽지 않는다. xlsx와 pptx는 라이브러리로 다시 저장하므로 차트나 그림이 유실될 수 있고, 그럴 때 경고가 나온다.
- 문서 내용은 모델 컨텍스트로 전송된다. 승인된 사내 모델 엔드포인트만 쓰도록 클라이언트를 구성해야 하며, 이 도구가 대신 보장하지 않는다.
- 훅은 안전장치이지 보안 경계가 아니다. 셸 명령은 문자열 검사라 우회할 수 있고, 보호 경로를 셸 인자로 넘기는 정상 명령도 막을 수 있다(알려진 오탐). 경계는 격리 환경과 NASCA 정책이 맡는다.
- 모의 NASCA는 보안 기능이 없다. 보호 파일을 흉내 내는 시험용이다.

## 개발

```bash
uv venv .venv && . .venv/bin/activate && uv pip install -e ".[dev]"
pytest -q                             # 자동 시험
python scripts/demo_cycles.py         # 암복호화 횟수 비교(수정 8번이면 8회에서 1회로)
python -m workbench.doctor --selftest # 설정과 백엔드 점검, 임시 문서로 전체 흐름 한 번
claude --plugin-dir .                 # 플러그인 로컬 시험
```

구조: `src/workbench/agent`는 격리 환경 안에서 도는 문서 에이전트, `src/workbench/broker`는 세션과 감사 로그와 백엔드, `src/workbench/nasca`는 NASCA 어댑터,
`enforcement`는 훅과 클라이언트 설정 예시, `runtime`은 샌드박스 런타임 스크립트다.
