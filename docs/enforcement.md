# AI가 작업구역만 쓰게 하는 방법

말로 시키는 것은 부탁이고, 막는 것은 강제다. 둘을 층으로 겹친다. 위 층이 뚫려도 아래 층이 남게 한다.
다만 강제가 일반 작업을 막아서는 안 된다. 아래 "일반 작업을 건드리지 않는 원칙"이 우선한다.

| 층 | 무엇을 하나 | 강제인가 | 어디서 |
| --- | --- | --- | --- |
| 1. 도구 표면 축소 | 원본 NASCA MCP를 AI 클라이언트에 등록하지 않고 중개 계층만 호출하게 한다 | 강제 | MCP 설정, `deniedMcpServers` |
| 2. 작업구역 격리 | 평문이 격리 VM 안에만 있고 나가는 길이 export 하나다 | 강제 | 백엔드, 방화벽 |
| 3. 접근 통제 훅 | 보호 폴더로 가는 Read, Edit, Write, Grep, Glob, Bash와 원본 NASCA MCP 호출을 막는다 | 안전장치 | `enforcement/hooks/guard.py` |
| 4. deny 규칙 | 보호 폴더 Read, Edit를 권한 규칙으로 막는다 | 강제(Grep, Glob은 최선) | `settings.json` |
| 5. 지침 | 서버 지침, 도구 설명, 결과 끝의 상태 줄, 세션 시작 컨텍스트, skill | 안내 | MCP 서버, 훅, 플러그인 |
| 6. 관리형 배포 | 위 설정을 사용자가 끄지 못하게 IT가 배포한다 | 강제 | managed-settings.json |

## 일반 작업을 건드리지 않는 원칙

- **정책이 없으면 무동작.** `policy.json`이 하나도 없으면 훅은 아무것도 막지 않고 아무것도 출력하지 않는다.
- **보호 폴더와 차단 MCP만.** 정책에 적힌 보호 폴더와 차단 MCP 이름에 해당하는 호출만 막는다. 그 밖의 읽기, 쓰기, 수정, 삭제, 검색, 셸 명령은 통과한다.
  이름이 비슷한 형제 폴더(`보안문서2`, `보안문서_백업`)와 이름에 `nasca`가 들어갔을 뿐인 다른 MCP 서버는 막지 않는다.
  파일 내용에 보호 경로 문자열이 들어 있는 코드나 설정 파일을 수정하는 것도 막지 않는다. 훅은 파일 경로만 본다.
- **오류가 나면 열어 둔다.** 정책 파일이 깨졌거나 훅에 오류가 나면 막지 않는다. 오류는 `%LOCALAPPDATA%\workbench\guard-error.log`에 남고 세션 시작 때 사용자에게 한 번 알린다.
  보호가 꺼진 채 일하게 되는 것보다 기본 작업이 막히는 것이 더 큰 피해라고 판단했다. 보호 상태 점검은 `python -m workbench.doctor`가 한다.
- **훅이 못 돌 때.** `python`이 PATH에 없으면 훅이 실행되지 않고 알림이 뜬다. 작업은 막히지 않는다. 설치 스크립트와 doctor가 이를 경고한다.
- **작업구역은 연 문서만.** `workbench_*`는 `workbench_open`으로 연 문서와 `workbench_create`로 만든 문서만 다룬다. 호스트 파일은 열 때 읽고 내보낼 때만 쓴다.

이 원칙은 `tests/test_normal_work_untouched.py`와 `tests/test_crud_separation.py`가 고정한다.

## 알려진 오탐

셸 명령은 문자열 검사라서, 보호 경로를 인자로 넘기는 명령은 읽기가 아니어도 막힌다. 예를 들어 소스에서 그 경로 문자열을 찾는
`grep -rn "D:/NASCA/보안문서" src/`도 막힌다. 막혔을 때 메시지가 이유를 알려 주므로 경로를 줄이거나 보호 폴더를 포함하지 않는 검색으로 바꾼다.
Grep과 Glob도 검색 범위가 보호 폴더를 품고 있으면(예: 보호 폴더의 상위 폴더 전체 검색) 막는다. `path`를 프로젝트 폴더로 좁히면 된다.

## 정책 파일

훅과 세션 시작 문구는 `policy.json`을 읽는다. 위치는 `WORKBENCH_POLICY`, 기계 전체(`%ProgramData%\workbench\policy.json`, 리눅스는 `/etc/workbench/policy.json`), 사용자(`%LOCALAPPDATA%\workbench\policy.json`) 순이고, 찾은 파일의 목록을 합친다.

```json
{ "protected_roots": ["D:/NASCA/보안문서"], "blocked_mcp_servers": ["nasca"] }
```

`enforcement/policy.example.json`을 복사해 쓴다. 설치 스크립트는 `-ProtectedRoot`로 이 파일을 만든다.

## 훅이 막는 것과 못 막는 것

막는다. 드라이브 표기, 역슬래시, 대소문자, `..`, WSL(`/mnt/d`)과 MSYS(`/d`) 표기, 홈 약칭(`~`, `$HOME`, `%USERPROFILE%`)을 같은 경로로 본다. 플러그인으로 들어온 MCP(`mcp__plugin_*_nasca__*`)도 막는다.

못 막는다.
- 셸 명령의 변수 조립(`$a$b`), 다른 폴더에서 `cd` 후 상대 경로, 심볼릭 링크 생성 같은 간접 방법.
- 훅이 없는 클라이언트(Codex 등).

그래서 훅만 믿지 않는다. 층 1, 2, 4, 6을 함께 쓰고, 근본적으로는 AI 프로세스가 보호 폴더를 읽을 수 없는 OS 권한이나 NASCA 프로세스 정책이 경계가 되어야 한다.

## Claude Code

개인이나 팀은 플러그인을 켠다. 훅과 MCP 서버와 지침이 한 번에 들어온다.

```bash
claude --plugin-dir .                                    # 시험(저장소 루트에서)
claude plugin marketplace add namojo/nasca-mcp           # 배포
claude plugin install workbench@nasca-mcp
```

IT 배포는 `enforcement/claude-code/managed-settings.example.json`을 `C:\Program Files\ClaudeCode\managed-settings.json`에 둔다. 담긴 것은 다음과 같다.
- `permissions.deny`: 보호 폴더 Read와 Edit
- `deniedMcpServers`: 원본 NASCA MCP
- `allowManagedPermissionRulesOnly`, `allowManagedHooksOnly`: 사용자 설정이 규칙과 훅을 바꾸지 못하게 함
- `permissions.disableBypassPermissionsMode`: 권한 우회 모드 끄기
- `enabledPlugins`: 작업구역 플러그인 강제 활성화. 이렇게 켠 플러그인의 훅은 `allowManagedHooksOnly`에서도 돈다.

`allowManagedPermissionRulesOnly`를 켜면 사용자와 프로젝트의 권한 규칙이 무시되므로, 일반 작업의 허용 규칙도 관리형에 함께 둬야 한다는 점에 주의한다.
배포한 뒤 `/status`와 `claude doctor`로 적용 여부를 본다. 잘못된 항목은 조용히 무시될 수 있다.
관리형 `allowManagedMcpServersOnly`가 플러그인이 주는 MCP 서버에도 적용되는지는 문서에서 확인하지 못했으니 켜기 전에 시험한다.
설정 키 이름은 Claude Code 문서를 따랐고, Windows 드라이브 경로를 쓰는 deny 규칙 표기(`Read(//D:/...)`)는 문서에 예시가 없어 확인하지 못했다. 적용 후 막히는지 직접 확인한다.

## Codex

Codex에서 PreToolUse 같은 훅 설정은 확인하지 못했다. 그래서 다음에 기댄다.
- `enforcement/codex/config.toml.example`로 작업구역 서버를 등록하고 원본 NASCA MCP는 `enabled = false`로 둔다.
- `workbench_export`는 `approval_mode = "prompt"`로 사람이 승인하게 한다.
- 보호 폴더 읽기 차단은 OS 권한이나 NASCA 정책에서 한다.

## 확인 방법

훅이 실제로 막는지는 정책을 만들고 플러그인을 켜서 헤드리스로 돌려 본다.

```bash
WORKBENCH_POLICY=/path/policy.json claude -p "보호 폴더의 a.txt를 Read로 읽어 줘" \
  --plugin-dir . --allowedTools "Read" --output-format json < /dev/null
```

개발 환경에서는 Read, Bash, Grep, Glob이 모두 `PreToolUse ... hook error: ... DRM 보호 폴더라서 ...` 메시지로 막히고 모델이 `workbench_open`을 제안하는 것을 확인했다.
같은 환경에서 보호되지 않은 파일은 읽혔다. 모델에게 "차단 경로를 차례로 찔러 보라"고 시키면 모델 쪽에서 거절하므로, 차단 시험은 일상 업무 문구로 한다.
