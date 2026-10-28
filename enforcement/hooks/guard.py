#!/usr/bin/env python3
"""Claude Code PreToolUse 훅. 보호 폴더의 문서를 AI가 직접 여는 길과 원본 NASCA MCP 호출을 막는다.

설계 원칙: 평소 작업을 방해하지 않는다.
- 정책 파일이 없으면 아무것도 막지 않는다.
- 막는 것은 정책에 적힌 보호 폴더와 차단 MCP 이름에 해당하는 호출뿐이다. 그 밖의 읽기, 쓰기, 수정, 삭제, 검색, 셸 명령은 그대로 통과한다.
- 정책 파일이 깨졌거나 훅 자체에 오류가 나도 막지 않는다(열어 둔다). 오류는 로그에 남기고, 세션 시작 때 사용자에게 한 번 알린다.
  보호 기능이 꺼진 채로 일하게 되는 것보다 기본 작업이 막히는 것이 더 큰 피해라고 판단했다. 점검은 doctor가 한다.

정책 파일을 찾는 순서: WORKBENCH_POLICY 환경변수, 기계 전체 정책(Windows는 ProgramData, 그 밖은 /etc),
사용자 정책(WORKBENCH_HOME 또는 기본 홈). 찾은 파일의 목록을 합친다.

한계: 이 훅은 보안 경계가 아니라 안전장치다. 셸 명령은 문자열 검사라서 변수나 간접 호출로 피해 갈 수 있다.
진짜 경계는 격리 환경과 NASCA 프로세스 정책이다.

표준 라이브러리만 쓴다. 어떤 파이썬으로도 돈다.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sys
import time
from pathlib import Path

FILE_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
SEARCH_TOOLS = {"Grep", "Glob"}
SHELL_TOOLS = {"Bash", "PowerShell"}

HINT = "보호 문서는 workbench_open으로 작업구역에 열고 workbench_* 도구로 읽고 고친다."

# 보호 경로 뒤에 올 수 있는 경계: 끝, 경로 구분자, 공백, 따옴표, 셸 기호. 이름이 이어지면(보안문서2) 다른 폴더다.
_BOUNDARY = r"""(?=$|[/\s"'`;|&<>(),=:*?\[\]{}])"""


# --- 경로 정규화 ------------------------------------------------------------
def norm(p: str) -> str:
    """비교용 문자열. 슬래시 통일, .. 정리, 소문자, 끝 슬래시 제거."""
    p = os.path.expandvars(os.path.expanduser(p.strip().strip('"').strip("'")))
    p = p.replace("\\", "/")
    drive = ""
    if len(p) > 1 and p[1] == ":":
        drive, p = p[:2], p[2:]
    p = posixpath.normpath(p) if p else "."
    return (drive + p).lower().rstrip("/") if p != "/" else (drive + p).lower()


def variants(root: str) -> set[str]:
    """한 경로를 쓰는 여러 표기: 드라이브, WSL, MSYS, 홈 약칭."""
    n = norm(root)
    out = {n}
    if len(n) > 1 and n[1] == ":":
        d, rest = n[0], n[2:]
        out.add(f"/mnt/{d}{rest}")
        out.add(f"/{d}{rest}")
    home = norm(str(Path.home()))
    if n == home or n.startswith(home + "/"):
        rel = n[len(home):]
        for prefix in ("~", "$home", "%userprofile%", "$env:userprofile"):
            out.add(prefix + rel)
    return out


def under(child: str, root: str) -> bool:
    return child == root or child.startswith(root.rstrip("/") + "/")


def mentions(text: str, root_variant: str) -> bool:
    """문자열 안에 보호 경로가 '경로로서' 나오는지. 이름이 비슷한 형제 폴더(보안문서2)는 해당하지 않는다."""
    return re.search(re.escape(root_variant) + _BOUNDARY, text) is not None


def resolve(path: str, cwd: str) -> list[str]:
    """상대 경로는 cwd 기준으로 풀고, 실제 경로(심볼릭 링크, 정션)도 함께 비교한다."""
    raw = os.path.expandvars(os.path.expanduser(path))
    cands = {norm(raw)}
    if cwd and not (raw.startswith(("/", "\\")) or (len(raw) > 1 and raw[1] == ":")):
        cands.add(norm(os.path.join(cwd, raw)))
    for c in list(cands):
        try:
            cands.add(norm(os.path.realpath(c)))
        except OSError:
            pass
    return list(cands)


# --- 정책 -------------------------------------------------------------------
def policy_files() -> list[Path]:
    files: list[Path] = []
    if os.environ.get("WORKBENCH_POLICY"):
        files.append(Path(os.environ["WORKBENCH_POLICY"]))
    if os.name == "nt":
        files.append(Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "workbench" / "policy.json")
    else:
        files.append(Path("/etc/workbench/policy.json"))
    home = os.environ.get("WORKBENCH_HOME")
    if home:
        files.append(Path(home) / "policy.json")
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        files.append(Path(os.environ["LOCALAPPDATA"]) / "workbench" / "policy.json")
    else:
        files.append(Path.home() / ".workbench" / "policy.json")
    return files


def load_policy() -> tuple[dict | None, str | None]:
    """(정책, 오류). 파일이 없으면 (None, None). 읽을 수 없으면 (None, 이유)."""
    merged = {"protected_roots": [], "blocked_mcp_servers": []}
    found = False
    for f in policy_files():
        if not f.is_file():
            continue
        found = True
        try:
            obj = json.loads(f.read_text(encoding="utf-8-sig"))
            for key in merged:
                vals = obj.get(key, [])
                if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
                    raise ValueError(f"{key}는 문자열 목록이어야 한다")
                merged[key].extend(vals)
        except (OSError, ValueError) as e:
            return None, f"정책 파일 {f}를 읽을 수 없다({type(e).__name__})"
    return (merged if found else None), None


def log_error(msg: str) -> None:
    """진단용 기록. 실패해도 조용히 넘어간다."""
    try:
        base = Path(os.environ.get("WORKBENCH_HOME") or (Path(os.environ.get("LOCALAPPDATA", "")) / "workbench"
                                                         if os.name == "nt" else Path.home() / ".workbench"))
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "guard-error.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# --- 판정 -------------------------------------------------------------------
def mcp_server_of(tool_name: str) -> str | None:
    if not tool_name.startswith("mcp__"):
        return None
    return tool_name[5:].split("__", 1)[0]


def decide(event: dict, policy: dict) -> str | None:
    """막아야 하면 사유를, 아니면 None을 돌려준다."""
    tool = event.get("tool_name", "")
    inp = event.get("tool_input") or {}
    cwd = event.get("cwd") or ""
    roots = [(r, variants(r)) for r in policy["protected_roots"]]

    server = mcp_server_of(tool)
    if server is not None:
        for blocked in policy["blocked_mcp_servers"]:
            b = blocked.lower()
            s = server.lower()
            if s == b or (s.startswith("plugin_") and s.endswith("_" + b)):
                return f"'{blocked}' MCP는 작업구역 서버만 호출할 수 있다. 직접 호출은 막혀 있다. {HINT}"
        return None

    if tool in FILE_TOOLS:
        path = inp.get(FILE_TOOLS[tool], "")
        for cand in resolve(path, cwd) if path else []:
            for root, vs in roots:
                if any(under(cand, v) for v in vs):
                    return f"'{root}'는 DRM 보호 폴더라서 {tool}로 직접 접근할 수 없다. {HINT}"
        return None

    if tool in SEARCH_TOOLS:
        base = inp.get("path") or cwd
        texts = [str(inp.get(k, "")) for k in ("pattern", "glob")]
        for cand in resolve(base, cwd) if base else []:
            for root, vs in roots:
                for v in vs:
                    if under(cand, v):
                        return f"'{root}'는 DRM 보호 폴더라서 {tool}로 검색할 수 없다. {HINT}"
                    if under(v, cand):  # 검색 범위가 보호 폴더를 품고 있다
                        return (f"검색 범위에 DRM 보호 폴더('{root}')가 들어 있어 {tool}를 실행할 수 없다. "
                                "path를 보호 폴더를 포함하지 않는 폴더(예: 프로젝트 폴더)로 좁혀 다시 실행한다.")
        for t in texts:
            nt = t.replace("\\", "/").lower()
            for root, vs in roots:
                if any(mentions(nt, v) for v in vs):
                    return f"'{root}'는 DRM 보호 폴더라서 {tool}로 검색할 수 없다. {HINT}"
        return None

    if tool in SHELL_TOOLS:
        cmd = str(inp.get("command", "")).replace("\\", "/").lower()
        for root, vs in roots:
            if cwd and any(under(c, v) for c in resolve(cwd, cwd) for v in vs):
                return f"작업 폴더가 DRM 보호 폴더('{root}') 안이라서 {tool} 명령을 실행할 수 없다. {HINT}"
            if any(mentions(cmd, v) for v in vs):
                return f"명령이 DRM 보호 폴더('{root}')를 가리켜서 {tool}로 실행할 수 없다. {HINT}"
        return None

    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        policy, err = load_policy()
        if err:
            log_error(err)  # 열어 둔다. 사용자에게는 세션 시작 때 알린다.
            return 0
        if not policy or not (policy["protected_roots"] or policy["blocked_mcp_servers"]):
            return 0
        reason = decide(event, policy)
    except Exception as e:  # noqa: BLE001  훅의 오류가 사용자의 기본 작업을 막으면 안 된다
        log_error(f"guard 오류 {type(e).__name__}: {e}")
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
