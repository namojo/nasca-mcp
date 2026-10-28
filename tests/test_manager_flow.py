"""작업구역 전체 흐름: 한 번 복호화 → 여러 번 읽기·수정 → 한 번 재보호 → 평문 삭제."""
import os
import time
from pathlib import Path

import pytest
from docx import Document

from workbench.broker.audit import read_events, verify
from workbench.errors import WorkbenchError
from workbench.nasca.mock import is_protected, unprotect_bytes


def test_decrypt_once_edit_many_protect_once(manager, protected_docx, tmp_path):
    opened = manager.open(str(protected_docx))
    assert opened["was_drm_protected"] and opened["kind"] == "docx"

    for find, rep in [("REST", "gRPC"), ("OAuth2", "mTLS")]:
        manager.edit("설계서.docx", [{"op": "replace_text", "find": find, "replace": rep}])
    manager.read("설계서.docx")
    manager.edit("설계서.docx", [{"op": "insert_after", "i": 0, "text": "개정: 2026-10"}])

    out = tmp_path / "out" / "설계서_v2.docx"
    exported = manager.export("설계서.docx", str(out))
    assert Path(exported["path"]) == out
    assert is_protected(out.read_bytes()), "내보낸 파일은 다시 보호돼야 한다"

    status = manager.status()
    assert status["counters"]["decrypts"] == 1
    assert status["counters"]["protects"] == 1
    assert status["counters"]["edits"] == 3

    # 재보호본을 복호화하면 수정 내용이 들어 있다
    import io
    d = Document(io.BytesIO(unprotect_bytes(out.read_bytes())))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "gRPC" in text and "mTLS" in text and "개정: 2026-10" in text

    # 원본 보호 파일은 그대로다
    assert is_protected(protected_docx.read_bytes())


def test_close_blocks_unexported_changes_then_discards(manager, protected_docx):
    manager.open(str(protected_docx))
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])
    with pytest.raises(WorkbenchError, match="내보내지 않은 수정"):
        manager.close()
    summary = manager.close(discard=True)
    assert summary["discarded_unexported"] == ["설계서.docx"]
    assert manager.session is None


def test_close_wipes_plaintext_and_stops_agent(manager, protected_docx):
    manager.open(str(protected_docx))
    backend = manager.session.backend
    root = Path(backend.root)
    assert any(root.rglob("*.docx")), "작업 중에는 평문 사본이 있다"
    proc = backend.proc
    manager.close(discard=True)
    assert not root.exists(), "닫으면 작업 루트가 사라진다"
    assert proc.poll() is not None, "에이전트 프로세스가 끝나야 한다"


def test_tools_fail_clearly_without_session(manager):
    with pytest.raises(WorkbenchError, match="workbench_open"):
        manager.read("a.docx")


def test_after_expiry_message_names_lost_changes(settings, nasca, protected_docx):
    from workbench.broker.manager import Manager

    settings.idle_ttl_s = 1.0
    m = Manager(settings, nasca)
    try:
        m.open(str(protected_docx))
        m.edit("설계서.docx", [{"op": "replace_text", "find": "REST", "replace": "gRPC"}])
        deadline = time.time() + 15
        while m.session is not None and time.time() < deadline:
            time.sleep(0.2)
        assert m.session is None, "유휴 시간이 지나면 자동 종료돼야 한다"
        with pytest.raises(WorkbenchError) as ei:
            m.read("설계서.docx")
        assert "유휴 시간 초과" in str(ei.value) and "설계서.docx" in str(ei.value)
    finally:
        m.shutdown()


def test_open_and_export_roots_are_enforced(settings, nasca, protected_docx, tmp_path):
    from workbench.broker.manager import Manager

    settings.open_roots = [tmp_path / "drm"]
    settings.export_roots = [tmp_path / "out"]
    m = Manager(settings, nasca)
    try:
        outside = tmp_path / "other.docx"
        outside.write_bytes(b"x")
        with pytest.raises(WorkbenchError, match="허용된 폴더 밖"):
            m.open(str(outside))
        m.open(str(protected_docx))
        with pytest.raises(WorkbenchError, match="허용된 폴더 밖"):
            m.export("설계서.docx", str(tmp_path / "elsewhere" / "x.docx"))
        m.export("설계서.docx", str(tmp_path / "out" / "ok.docx"))
    finally:
        m.shutdown()


def test_duplicate_open_and_bad_names_rejected(manager, protected_docx):
    manager.open(str(protected_docx))
    with pytest.raises(WorkbenchError, match="이미 있습니다"):
        manager.open(str(protected_docx))
    with pytest.raises(WorkbenchError, match="상위 폴더"):
        manager.open(str(protected_docx), name="../escape.docx")


def test_export_refuses_overwrite_unless_asked(manager, protected_docx, tmp_path):
    manager.open(str(protected_docx))
    out = tmp_path / "out" / "a.docx"
    manager.export("설계서.docx", str(out))
    with pytest.raises(WorkbenchError, match="이미 있습니다"):
        manager.export("설계서.docx", str(out))
    manager.export("설계서.docx", str(out), overwrite=True)


def test_image_is_viewable_and_cropped(manager, protected_png):
    manager.open(str(protected_png))
    full = manager.view_image("구성도.png", max_side=600)
    assert full["width"] == 600 and full["original_width"] == 1200
    part = manager.view_image("구성도.png", crop_frac=[0, 0, 0.5, 0.5])
    assert part["crop_box"] == [0, 0, 600, 400]
    assert part["data"][:3] in (b"\xff\xd8\xff", b"\x89PN")


def test_audit_chain_is_valid_and_has_no_content(manager, protected_docx, tmp_path):
    manager.open(str(protected_docx))
    manager.edit("설계서.docx", [{"op": "replace_text", "find": "OAuth2", "replace": "비밀문구-XYZ"}])
    log = manager.session.audit.path
    manager.close(discard=True)
    ok, bad = verify(log)
    assert ok and bad is None
    raw = Path(log).read_text(encoding="utf-8")
    assert "비밀문구-XYZ" not in raw and "OAuth2" not in raw, "감사 로그에 문서 내용이 들어가면 안 된다"
    events = [e["event"] for e in read_events(log)]
    assert events == ["session_start", "open", "edit", "session_end"]

    # 한 글자만 바꿔도 체인 검증이 실패한다
    tampered = raw.replace('"event": "open"', '"event": "opeN"')
    Path(log).write_text(tampered, encoding="utf-8")
    assert verify(log)[0] is False
