import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "enforcement" / "hooks"
spec = importlib.util.spec_from_file_location("guard", HOOKS / "guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

POLICY = {"protected_roots": ["D:/NASCA/보안문서", "~/Documents/NASCA"], "blocked_mcp_servers": ["nasca"]}


def ev(tool, cwd="/work/proj", **inp):
    return {"tool_name": tool, "tool_input": inp, "cwd": cwd}


@pytest.mark.parametrize("event", [
    ev("Read", file_path="D:/NASCA/보안문서/설계서.docx"),
    ev("Read", file_path="d:\\nasca\\보안문서\\설계서.docx"),                    # 역슬래시, 대소문자
    ev("Read", file_path="D:/NASCA/보안문서/../보안문서/a.docx"),               # .. 우회
    ev("Edit", file_path="/mnt/d/NASCA/보안문서/a.docx", old_string="a", new_string="b"),  # WSL 표기
    ev("Write", file_path=str(Path.home() / "Documents/NASCA/new.docx")),
    ev("Read", cwd="D:/NASCA/보안문서", file_path="a.docx"),                    # 상대 경로 + cwd
    ev("Grep", path="D:/NASCA/보안문서", pattern="계약"),
    ev("Grep", path="D:/", pattern="계약"),                                   # 보호 폴더를 품은 상위 폴더 전체 검색
    ev("Glob", pattern="D:/NASCA/보안문서/**/*.docx"),
    ev("Bash", command='cat "D:\\NASCA\\보안문서\\a.txt"'),
    ev("Bash", command="python -c \"open('/mnt/d/NASCA/보안문서/a.docx','rb')\""),
    ev("Bash", command="cp ~/Documents/NASCA/a.docx /tmp/"),                # 홈 약칭
    ev("Bash", command="type %USERPROFILE%\\Documents\\NASCA\\a.txt"),
    ev("Bash", cwd="D:/NASCA/보안문서", command="ls"),
    ev("PowerShell", command="Get-Content $HOME/Documents/NASCA/a.txt"),
    {"tool_name": "mcp__nasca__decrypt", "tool_input": {}, "cwd": "/w"},
    {"tool_name": "mcp__plugin_x_nasca__decrypt", "tool_input": {}, "cwd": "/w"},
])
def test_blocked(event):
    reason = guard.decide(event, POLICY)
    assert reason and ("workbench" in reason or "검색 범위" in reason), event


@pytest.mark.parametrize("event", [
    ev("Read", file_path="/work/proj/README.md"),
    ev("Read", file_path="D:/NASCA/공개자료/a.docx"),                          # 이름이 비슷한 다른 폴더
    ev("Read", file_path="D:/NASCA/보안문서2/a.docx"),                         # 접두사만 같은 폴더
    ev("Grep", path="/work/proj", pattern="x"),
    ev("Bash", command="git status && pytest -q"),
    ev("Edit", file_path="/work/proj/src/a.py", old_string="a", new_string="b"),
    {"tool_name": "mcp__workbench__workbench_open", "tool_input": {"source_path": "D:/NASCA/보안문서/a.docx"}, "cwd": "/w"},
    {"tool_name": "mcp__plugin_workbench_workbench__workbench_read", "tool_input": {}, "cwd": "/w"},
    {"tool_name": "mcp__github__list_issues", "tool_input": {}, "cwd": "/w"},
    {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}, "cwd": "/w"},
])
def test_allowed(event):
    assert guard.decide(event, POLICY) is None, event


def run_hook(event, env_extra):
    env = {**os.environ, **env_extra}
    p = subprocess.run([sys.executable, str(HOOKS / "guard.py")], input=json.dumps(event, ensure_ascii=False).encode(),
                       capture_output=True, env=env, timeout=30)
    return p.returncode, p.stdout.decode("utf-8")


def test_hook_process_denies_with_documented_json(tmp_path):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(POLICY), encoding="utf-8")
    code, out = run_hook(ev("Read", file_path="D:/NASCA/보안문서/a.docx"), {"WORKBENCH_POLICY": str(pol)})
    assert code == 0
    body = json.loads(out)["hookSpecificOutput"]
    assert body["hookEventName"] == "PreToolUse" and body["permissionDecision"] == "deny"
    assert "workbench_open" in body["permissionDecisionReason"]


def test_hook_process_allows_silently(tmp_path):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(POLICY), encoding="utf-8")
    assert run_hook(ev("Read", file_path="/work/proj/a.py"), {"WORKBENCH_POLICY": str(pol)}) == (0, "")


def test_no_policy_means_no_blocking(tmp_path):
    env = {"WORKBENCH_HOME": str(tmp_path / "none"), "WORKBENCH_POLICY": str(tmp_path / "missing.json")}
    assert run_hook(ev("Read", file_path="D:/NASCA/보안문서/a.docx"), env) == (0, "")


def test_broken_policy_never_blocks_basic_work_and_is_reported_at_session_start(tmp_path):
    pol = tmp_path / "policy.json"
    pol.write_text("{ not json", encoding="utf-8")
    env = {"WORKBENCH_POLICY": str(pol), "WORKBENCH_HOME": str(tmp_path / "wbhome")}
    for event in (ev("Bash", command="ls"), ev("Read", file_path="/work/proj/a.py"),
                  ev("Edit", file_path="/work/proj/a.py", old_string="a", new_string="b"),
                  ev("Write", file_path="/work/proj/new.txt", content="x")):
        assert run_hook(event, env) == (0, ""), "정책이 깨져도 기본 작업은 막히지 않는다"
    assert "정책 파일" in (tmp_path / "wbhome" / "guard-error.log").read_text(encoding="utf-8")
    p = subprocess.run([sys.executable, str(HOOKS / "session_start.py")], capture_output=True, timeout=30,
                       env={**os.environ, **env})
    msg = json.loads(p.stdout.decode("utf-8"))["systemMessage"]
    assert "보호 폴더 차단이 꺼진 상태" in msg


def test_garbage_input_does_not_crash():
    p = subprocess.run([sys.executable, str(HOOKS / "guard.py")], input=b"not json", capture_output=True, timeout=30)
    assert p.returncode == 0


def test_session_start_states_facts(tmp_path):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(POLICY), encoding="utf-8")
    p = subprocess.run([sys.executable, str(HOOKS / "session_start.py")], capture_output=True, timeout=30,
                       env={**os.environ, "WORKBENCH_POLICY": str(pol)})
    ctx = json.loads(p.stdout.decode("utf-8"))["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart" and "D:/NASCA/보안문서" in ctx["additionalContext"]
    assert len(ctx["additionalContext"]) < 10000
