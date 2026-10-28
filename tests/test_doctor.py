import json
import sys
from pathlib import Path

from workbench.doctor import main

FAKE = str(Path(__file__).with_name("fake_nasca_server.py"))


def run(capsys, monkeypatch, tmp_path, argv=(), **env):
    monkeypatch.setenv("WORKBENCH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WORKBENCH_BACKEND", "local")
    monkeypatch.setenv("WORKBENCH_POLICY", str(tmp_path / "none.json"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_local_mock_warns_but_passes(capsys, monkeypatch, tmp_path):
    code, out = run(capsys, monkeypatch, tmp_path)
    assert code == 0
    assert "[경고] 백엔드가 local" in out and "NASCA 어댑터가 mock" in out


def test_selftest_runs_whole_flow(capsys, monkeypatch, tmp_path):
    code, out = run(capsys, monkeypatch, tmp_path, ["--selftest"])
    assert code == 0, out
    assert "격리 환경 안에서 문서 수정" in out and "닫기와 평문 삭제" in out


def test_winsandbox_backend_errors_off_windows(capsys, monkeypatch, tmp_path):
    code, out = run(capsys, monkeypatch, tmp_path, WORKBENCH_BACKEND="winsandbox")
    assert code == 1 and "Windows가 아닙니다" in out


def test_probe_lists_nasca_tools_and_flags_wrong_tool_name(capsys, monkeypatch, tmp_path):
    cfg = tmp_path / "nasca.json"
    cfg.write_text(json.dumps({
        "command": sys.executable, "args": [FAKE],
        "decrypt": {"tool": "nasca_open_wrong", "args": {"path": "{path}"}, "result": {"mode": "base64", "field": "x"}},
        "protect": {"tool": "nasca_protect", "args": {}},
    }), encoding="utf-8")
    code, out = run(capsys, monkeypatch, tmp_path, ["--probe-nasca"], WORKBENCH_NASCA="mcp",
                    WORKBENCH_NASCA_MCP_CONFIG=str(cfg))
    assert code == 1
    assert "NASCA MCP 도구 nasca_decrypt(path)" in out
    assert "decrypt.tool 'nasca_open_wrong'이(가) 서버에 없습니다" in out
    assert "protect.tool 'nasca_protect'" in out and "[OK]   설정의 protect.tool" in out


def test_policy_status_is_reported(capsys, monkeypatch, tmp_path):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({"protected_roots": ["D:/NASCA"], "blocked_mcp_servers": ["nasca"]}), encoding="utf-8")
    code, out = run(capsys, monkeypatch, tmp_path, WORKBENCH_POLICY=str(pol))
    assert "접근 통제 정책: 보호 폴더 1개" in out
