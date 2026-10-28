"""플러그인 패키지가 가리키는 파일과 이름이 실제로 맞는지 확인한다."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_manifest_and_mcp_names():
    plugin = load(".claude-plugin/plugin.json")
    assert re.fullmatch(r"[a-z0-9-]+", plugin["name"])
    servers = load(".mcp.json")["mcpServers"]
    assert list(servers) == ["workbench"]
    launcher = servers["workbench"]["args"][0]
    assert launcher == "${CLAUDE_PLUGIN_ROOT}/scripts/launch_mcp.py"
    assert (ROOT / "scripts" / "launch_mcp.py").is_file()


def test_marketplace_points_at_this_plugin():
    market = load(".claude-plugin/marketplace.json")
    assert market["name"] == "nasca-mcp"
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [("workbench", "./")]
    assert load(".claude-plugin/plugin.json")["name"] == "workbench"


def test_launcher_prefers_installed_venv_and_falls_back(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("launch_mcp", ROOT / "scripts" / "launch_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("NASCA_MCP_PYTHON", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mod.pick_python() == sys.executable, "가상환경이 없으면 지금 파이썬을 쓴다"
    fake = tmp_path / "py"
    fake.write_text("")
    monkeypatch.setenv("NASCA_MCP_PYTHON", str(fake))
    assert mod.pick_python() == str(fake)


def test_launcher_starts_a_working_mcp_server(tmp_path):
    """런처로 서버를 띄워 실제로 도구 목록이 나오는지 본다(플러그인이 하는 방식 그대로)."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**__import__("os").environ, "WORKBENCH_HOME": str(tmp_path / "h"), "WORKBENCH_BACKEND": "local",
           "WORKBENCH_NASCA": "mock", "NASCA_MCP_PYTHON": sys.executable}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "scripts" / "launch_mcp.py")], env=env)

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return {t.name for t in (await s.list_tools()).tools}

    names = asyncio.run(asyncio.wait_for(go(), 60))
    assert "workbench_open" in names and "workbench_export" in names


def test_hooks_point_to_existing_scripts():
    hooks = load("hooks/hooks.json")["hooks"]
    assert set(hooks) == {"PreToolUse", "SessionStart"}
    for groups in hooks.values():
        for g in groups:
            for h in g["hooks"]:
                m = re.search(r'\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)', h["command"])
                assert m and (ROOT / m.group(1)).is_file(), h["command"]


def test_pretooluse_matcher_covers_every_guarded_tool():
    matcher = load("hooks/hooks.json")["hooks"]["PreToolUse"][0]["matcher"]
    rx = re.compile(matcher)
    for tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob", "Bash", "PowerShell",
                 "mcp__nasca__decrypt", "mcp__plugin_x_nasca__decrypt"):
        assert rx.search(tool), tool
    assert not rx.search("TodoWrite")


def test_skill_has_description_and_names_real_tools():
    text = (ROOT / "skills/workbench-policy/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "description:" in text.split("---")[1]
    sys.path.insert(0, str(ROOT / "src"))
    from workbench.mcp_server import TOOL_NAMES
    for name in set(re.findall(r"workbench_[a-z_]+", text)):
        assert name in TOOL_NAMES, f"SKILL.md가 없는 도구를 언급한다: {name}"


def test_demo_script_runs_and_shows_the_difference():
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_cycles.py"), "--edits", "4"],
                       capture_output=True, text=True, timeout=120,
                       env={"WORKBENCH_HOME": str(ROOT / ".pytest_cache" / "wbhome"), "WORKBENCH_BACKEND": "local",
                            "WORKBENCH_NASCA": "mock", "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src")})
    assert p.returncode == 0, p.stderr
    rows = {l.split()[0]: l.split() for l in p.stdout.splitlines() if l.startswith(("매번", "작업구역"))}
    assert rows["매번"][2:4] == ["4", "4"], p.stdout
    assert rows["작업구역"][1:3] == ["1", "1"], p.stdout
