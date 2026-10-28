import json
from pathlib import Path

import pytest

from workbench.config import Settings, is_within
from workbench.errors import WorkbenchError


def load(tmp_path, env=None, user=None, machine=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if user is not None:
        (home / "config.json").write_text(json.dumps(user), encoding="utf-8")
    mpath = tmp_path / "machine.json"
    if machine is not None:
        mpath.write_text(json.dumps(machine), encoding="utf-8")
    return Settings.load(env={"WORKBENCH_HOME": str(home), **(env or {})}, machine_config=mpath)


def test_precedence_default_user_env_machine(tmp_path):
    s = load(tmp_path)
    assert s.idle_ttl_s == 900
    s = load(tmp_path, user={"idle_ttl_s": 100})
    assert s.idle_ttl_s == 100
    s = load(tmp_path, user={"idle_ttl_s": 100}, env={"WORKBENCH_IDLE_TTL_S": "50"})
    assert s.idle_ttl_s == 50
    s = load(tmp_path, user={"idle_ttl_s": 100}, env={"WORKBENCH_IDLE_TTL_S": "50"}, machine={"idle_ttl_s": 30})
    assert s.idle_ttl_s == 30, "기계 설정이 항상 이긴다"


def test_machine_config_locks_security_values(tmp_path):
    s = load(tmp_path, env={"WORKBENCH_OPEN_ROOTS": "", "WORKBENCH_BACKEND": "local", "WORKBENCH_NASCA": "mock"},
             machine={"open_roots": [str(tmp_path / "drm")], "backend": "winsandbox", "nasca": "mcp",
                      "nasca_mcp_config": str(tmp_path / "n.json")})
    assert s.open_roots == [tmp_path / "drm"] and s.backend == "winsandbox" and s.nasca == "mcp"
    assert s.nasca_mcp_config == tmp_path / "n.json"


def test_env_lists_split_on_pathsep(tmp_path):
    import os
    s = load(tmp_path, env={"WORKBENCH_EXPORT_ROOTS": os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])})
    assert s.export_roots == [tmp_path / "a", tmp_path / "b"]


def test_bad_values_and_files_are_explained(tmp_path):
    with pytest.raises(WorkbenchError, match="idle_ttl_s"):
        load(tmp_path, env={"WORKBENCH_IDLE_TTL_S": "abc"})
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text("{ nope", encoding="utf-8")
    with pytest.raises(WorkbenchError, match="읽을 수 없습니다"):
        Settings.load(env={"WORKBENCH_HOME": str(home)}, machine_config=tmp_path / "none.json")


def test_unknown_keys_are_ignored(tmp_path):
    assert load(tmp_path, user={"typo_key": 1}).memory_mb == 4096


def test_is_within_rejects_prefix_siblings_and_traversal(tmp_path):
    root = tmp_path / "drm"
    (root / "sub").mkdir(parents=True)
    (tmp_path / "drm2").mkdir()
    assert is_within(root / "sub" / "a.docx", [root])
    assert not is_within(tmp_path / "drm2" / "a.docx", [root])
    assert not is_within(root / ".." / "drm2" / "a.docx", [root])
    assert is_within(tmp_path / "anything", [])
