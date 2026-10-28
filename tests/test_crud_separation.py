"""작업구역의 저장, 수정, 삭제가 호스트의 일반 파일과 엇갈리지 않는다."""
import hashlib
import os
import time
from pathlib import Path

import pytest

from workbench.errors import WorkbenchError
from workbench.nasca.mock import is_protected


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


def test_host_files_are_untouched_except_the_export_target(manager, protected_docx, tmp_path):
    """열기, 읽기, 수정, 삭제, 되돌리기를 해도 호스트 폴더의 파일은 하나도 바뀌지 않는다."""
    plain = tmp_path / "plain" / "메모.txt"
    plain.parent.mkdir()
    plain.write_text("일반 파일", encoding="utf-8")
    before = {"drm": snapshot(protected_docx.parent), "plain": snapshot(plain.parent)}

    manager.open(str(protected_docx))
    manager.read("설계서.docx")
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])
    manager.undo("설계서.docx")
    manager.edit("설계서.docx", [{"op": "set_text", "i": 1, "text": "다시"}])
    manager.create("새문서.txt", "내용")
    manager.delete("새문서.txt", discard=True)
    manager.delete("설계서.docx", discard=True)

    assert snapshot(protected_docx.parent) == before["drm"], "원본 보호 파일이 그대로여야 한다(임시 파일도 남기지 않는다)"
    assert snapshot(plain.parent) == before["plain"]
    manager.close(discard=True)


def test_workbench_delete_never_deletes_the_host_original(manager, protected_docx):
    manager.open(str(protected_docx))
    manager.delete("설계서.docx")
    assert protected_docx.is_file() and is_protected(protected_docx.read_bytes())


def test_delete_of_unexported_edits_needs_confirmation(manager, protected_docx):
    manager.open(str(protected_docx))
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])
    with pytest.raises(WorkbenchError, match="내보내지 않은 수정"):
        manager.delete("설계서.docx")
    assert "설계서.docx" in manager.session.files
    manager.delete("설계서.docx", discard=True)


def test_same_source_cannot_be_opened_twice(manager, protected_docx):
    """같은 원본을 두 이름으로 열면 사본이 둘로 갈라진다."""
    manager.open(str(protected_docx))
    with pytest.raises(WorkbenchError, match="이미 '설계서.docx'로 열려 있습니다"):
        manager.open(str(protected_docx), name="복사본.docx")
    # 지운 뒤에는 다시 열 수 있다
    manager.delete("설계서.docx")
    manager.open(str(protected_docx), name="복사본.docx")


def test_export_over_original_is_refused_if_original_changed_meanwhile(manager, protected_docx, nasca):
    manager.open(str(protected_docx))
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])

    # 다른 곳(Word 등)에서 원본이 바뀐다
    time.sleep(0.01)
    other = nasca.decrypt(str(protected_docx)).data
    nasca.protect(other + b"", str(protected_docx), overwrite=True)
    os.utime(protected_docx, ns=(time.time_ns(), time.time_ns() + 5_000_000))
    changed = protected_docx.read_bytes()

    with pytest.raises(WorkbenchError, match="다른 곳에서 바뀌었습니다"):
        manager.export("설계서.docx", str(protected_docx), overwrite=True)
    assert protected_docx.read_bytes() == changed, "거부했으면 원본은 그대로여야 한다"

    manager.export("설계서.docx", str(protected_docx), overwrite=True, force=True)
    assert protected_docx.read_bytes() != changed


def test_repeated_exports_to_the_original_are_not_conflicts(manager, protected_docx):
    manager.open(str(protected_docx))
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])
    manager.export("설계서.docx", str(protected_docx), overwrite=True)
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "gRPC", "replace": "GraphQL"}])
    manager.export("설계서.docx", str(protected_docx), overwrite=True)  # 우리가 쓴 결과가 새 기준


def test_export_to_a_new_path_never_checks_the_original(manager, protected_docx, tmp_path, nasca):
    manager.open(str(protected_docx))
    nasca.protect(b"changed elsewhere", str(protected_docx), overwrite=True)
    manager.export("설계서.docx", str(tmp_path / "out" / "새이름.docx"))  # 원본과 무관한 새 경로는 충돌이 아니다


def test_export_without_overwrite_still_refuses_existing_target(manager, protected_docx):
    manager.open(str(protected_docx))
    with pytest.raises(WorkbenchError, match="이미 있습니다"):
        manager.export("설계서.docx", str(protected_docx))


def test_server_instructions_tell_the_model_where_the_boundary_is():
    from workbench.manifest import RULES, SERVER_INSTRUCTIONS

    assert any("일반 파일은 평소처럼" in r for r in RULES)
    assert "일반 파일은 평소처럼" in SERVER_INSTRUCTIONS and len(SERVER_INSTRUCTIONS) < 4096
