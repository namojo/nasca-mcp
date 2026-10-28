import sys
from pathlib import Path

import pytest

from workbench.errors import WorkbenchError
from workbench.nasca.mcp_bridge import McpNasca
from workbench.nasca.mock import is_protected, unprotect_bytes

FAKE = str(Path(__file__).with_name("fake_nasca_server.py"))


def cfg(**over):
    c = {
        "command": sys.executable, "args": [FAKE], "call_timeout_s": 30,
        "decrypt": {"tool": "nasca_decrypt", "args": {"path": "{path}"},
                    "result": {"mode": "base64", "field": "content_base64", "label_field": "grade"}},
        "protect": {"tool": "nasca_protect", "input": "base64",
                    "args": {"path": "{dest}", "content_base64": "{data_b64}", "overwrite": "{overwrite}"},
                    "result": {"path_field": "path"}},
    }
    c.update(over)
    return c


@pytest.fixture
def adapter():
    a = McpNasca(cfg())
    yield a
    a.close()


def test_decrypt_base64_with_label(adapter, protected_docx):
    res = adapter.decrypt(str(protected_docx))
    assert res.protected and res.label == "대외비" and res.data[:2] == b"PK"


def test_connection_is_reused_across_calls(adapter, protected_docx):
    adapter.decrypt(str(protected_docx))
    first = adapter._session
    adapter.decrypt(str(protected_docx))
    assert adapter._session is first


def test_protect_roundtrip_and_overwrite_flag(adapter, tmp_path):
    dest = tmp_path / "o" / "x.bin"
    assert adapter.protect(b"hello", str(dest)) == str(dest)
    assert is_protected(dest.read_bytes()) and unprotect_bytes(dest.read_bytes()) == b"hello"
    with pytest.raises(WorkbenchError, match="NASCA MCP가 오류"):
        adapter.protect(b"again", str(dest))
    adapter.protect(b"again", str(dest), overwrite=True)  # 불리언이 문자열로 바뀌지 않고 전달된다


def test_tool_error_becomes_workbench_error(adapter, tmp_path):
    with pytest.raises(WorkbenchError, match="파일 없음"):
        adapter.decrypt(str(tmp_path / "none.docx"))


def test_wrong_field_name_explains_actual_fields(protected_docx):
    bad = cfg(decrypt={"tool": "nasca_decrypt", "args": {"path": "{path}"},
                       "result": {"mode": "base64", "field": "nope"}})
    a = McpNasca(bad)
    try:
        with pytest.raises(WorkbenchError, match="content_base64"):
            a.decrypt(str(protected_docx))
    finally:
        a.close()


def test_file_mode_reads_then_shreds_plaintext(protected_docx):
    c = cfg(decrypt={"tool": "nasca_decrypt_to_file", "args": {"path": "{path}"},
                     "result": {"mode": "file", "field": "decrypted_path", "delete_after": True}})
    a = McpNasca(c)
    try:
        # 서버가 만든 임시 파일이 읽은 즉시 지워지는지 확인하려고 경로를 가로챈다
        seen = []
        orig = a._call

        def spy(tool, args):
            r = orig(tool, args)
            from workbench.nasca.mcp_bridge import _payload
            seen.append(_payload(r)["decrypted_path"])
            return r

        a._call = spy
        res = a.decrypt(str(protected_docx))
        assert res.data[:2] == b"PK"
        assert seen and not Path(seen[0]).exists()
    finally:
        a.close()


def test_file_input_mode_for_protect_cleans_temp(tmp_path):
    c = cfg(protect={"tool": "nasca_protect_file", "input": "file", "args": {"path": "{dest}", "src": "{src}"},
                     "result": {"path_field": "path"}}, temp_dir=str(tmp_path))
    a = McpNasca(c)
    try:
        dest = tmp_path / "out.bin"
        a.protect(b"plain", str(dest))
        assert unprotect_bytes(dest.read_bytes()) == b"plain"
        assert not [p for p in tmp_path.iterdir() if p.name.startswith(".wb-")], "임시 평문 파일이 남으면 안 된다"
    finally:
        a.close()


def test_server_that_cannot_start_gives_actionable_error():
    a = McpNasca(cfg(command="/nonexistent/binary", args=[]))
    with pytest.raises(WorkbenchError, match="시작하지 못했습니다"):
        a.decrypt("x")


def test_missing_config_keys():
    with pytest.raises(WorkbenchError, match="decrypt"):
        McpNasca({"command": "x", "protect": {}})


def test_manager_end_to_end_with_mcp_adapter(settings, protected_docx, tmp_path):
    from workbench.broker.manager import Manager

    m = Manager(settings, McpNasca(cfg()))
    try:
        opened = m.open(str(protected_docx))
        assert opened["was_drm_protected"]
        m.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])
        out = m.export("설계서.docx", str(tmp_path / "o" / "r.docx"))
        assert is_protected(Path(out["path"]).read_bytes())
        assert m.status()["counters"] == {"decrypts": 1, "protects": 1, "reads": 0, "edits": 1}
    finally:
        m.shutdown()
