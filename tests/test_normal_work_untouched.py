"""일반 파일의 읽기, 쓰기, 수정, 삭제는 훅과 작업구역 때문에 달라지지 않는다.

기준: 보호 폴더와 차단 MCP에 해당하지 않는 호출은 어떤 경우에도 막히지 않는다.
"""
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
P = "/work/proj"


def ev(tool, cwd=P, **inp):
    return {"tool_name": tool, "tool_input": inp, "cwd": cwd}


ORDINARY = [
    # 읽기
    ev("Read", file_path=f"{P}/README.md"),
    ev("Read", file_path="C:/Users/me/Desktop/메모.txt"),
    ev("Read", file_path="notes.md"),
    # 쓰기와 수정
    ev("Write", file_path=f"{P}/out/result.json", content='{"path": "D:/NASCA/보안문서/a.docx"}'),  # 내용에 경로 문자열이 있어도
    ev("Edit", file_path=f"{P}/config.py", old_string="x", new_string="ROOT = 'D:/NASCA/보안문서'"),
    ev("MultiEdit", file_path=f"{P}/a.py", edits=[]),
    ev("NotebookEdit", notebook_path=f"{P}/a.ipynb", new_source="x"),
    # 이름이 비슷한 폴더(접두사 공유)
    ev("Read", file_path="D:/NASCA/보안문서2/a.docx"),
    ev("Write", file_path="D:/NASCA/보안문서_백업/a.docx", content="x"),
    ev("Edit", file_path="D:/NASCA/공개/a.docx", old_string="a", new_string="b"),
    ev("Read", file_path=str(Path.home() / "Documents/NASCA-notes/a.txt")),
    ev("Read", file_path=str(Path.home() / "Documents/NASCA2/a.txt")),
    # 검색
    ev("Grep", path=P, pattern="TODO"),
    ev("Grep", pattern="def main", glob="*.py"),
    ev("Glob", pattern="**/*.py"),
    ev("Glob", pattern=f"{P}/src/**/*.ts"),
    ev("Glob", pattern="D:/NASCA/보안문서2/*.docx"),
    # 셸: 삭제, 복사, 이동, 빌드, 테스트
    ev("Bash", command="rm -rf build dist && mkdir -p out"),
    ev("Bash", command="cp a.txt b.txt && mv b.txt c.txt"),
    ev("Bash", command="git status && git diff --stat && pytest -q"),
    ev("Bash", command="python scripts/run.py --out ./result"),
    ev("Bash", command='cat "D:\\NASCA\\보안문서_백업\\a.txt"'),            # 형제 폴더
    ev("Bash", command="ls D:/NASCA/보안문서2"),
    ev("Bash", command="ls ~/Documents/NASCA-notes"),
    ev("PowerShell", command="Get-ChildItem C:\\Users\\me\\Desktop | Remove-Item -Recurse -WhatIf"),
    ev("PowerShell", command="Set-Content -Path .\\out.txt -Value 'hello NASCA'"),
    ev("Bash", command="echo NASCA DRM 설명 문서 작성"),                       # 단어만
    # 다른 MCP
    {"tool_name": "mcp__github__create_issue", "tool_input": {}, "cwd": P},
    {"tool_name": "mcp__plugin_workbench_workbench__workbench_edit", "tool_input": {}, "cwd": P},
    {"tool_name": "mcp__nascadocs__search", "tool_input": {}, "cwd": P},      # 이름에 nasca가 들어갔을 뿐인 다른 서버
    {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}, "cwd": P},
    {"tool_name": "TodoWrite", "tool_input": {}, "cwd": P},
]


@pytest.mark.parametrize("event", ORDINARY, ids=lambda e: f"{e['tool_name']}:{json.dumps(e['tool_input'], ensure_ascii=False)[:60]}")
def test_ordinary_calls_are_never_blocked(event):
    assert guard.decide(event, POLICY) is None


def run_hook(event, env_extra):
    env = {**os.environ, **env_extra}
    p = subprocess.run([sys.executable, str(HOOKS / "guard.py")], input=json.dumps(event, ensure_ascii=False).encode(),
                       capture_output=True, env=env, timeout=30)
    return p.returncode, p.stdout.decode("utf-8")


def test_without_policy_nothing_is_ever_blocked(tmp_path):
    env = {"WORKBENCH_HOME": str(tmp_path / "none"), "WORKBENCH_POLICY": str(tmp_path / "missing.json")}
    for event in ORDINARY + [ev("Read", file_path="D:/NASCA/보안문서/a.docx"), ev("Bash", command="rm -rf D:/NASCA/보안문서")]:
        assert run_hook(event, env) == (0, "")


def test_empty_policy_blocks_nothing(tmp_path):
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps({"protected_roots": [], "blocked_mcp_servers": []}), encoding="utf-8")
    assert run_hook(ev("Read", file_path="D:/NASCA/보안문서/a.docx"), {"WORKBENCH_POLICY": str(pol)}) == (0, "")


def test_hook_bug_does_not_block(tmp_path, monkeypatch):
    """훅 안에서 예외가 나도 일반 작업은 통과한다(입력이 이상한 경우)."""
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps(POLICY), encoding="utf-8")
    env = {"WORKBENCH_POLICY": str(pol), "WORKBENCH_HOME": str(tmp_path / "h")}
    weird = {"tool_name": "Read", "tool_input": {"file_path": 12345}, "cwd": None}
    assert run_hook(weird, env) == (0, "")


def test_protected_calls_still_blocked_with_same_policy(tmp_path):
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps(POLICY), encoding="utf-8")
    code, out = run_hook(ev("Read", file_path="D:/NASCA/보안문서/a.docx"), {"WORKBENCH_POLICY": str(pol)})
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_known_false_positive_is_documented():
    """보호 경로 문자열을 셸 인자로 넘기면(예: grep으로 그 문자열 찾기) 막힌다. 이 동작은 문서에 적혀 있어야 한다."""
    assert guard.decide(ev("Bash", command='grep -rn "D:/NASCA/보안문서" src/'), POLICY) is not None
    doc = (Path(__file__).resolve().parents[1] / "docs" / "enforcement.md").read_text(encoding="utf-8")
    assert "알려진 오탐" in doc
