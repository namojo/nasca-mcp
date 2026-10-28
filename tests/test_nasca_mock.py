import pytest

from workbench.errors import WorkbenchError
from workbench.nasca.mock import MockNasca, is_protected, protect_bytes, unprotect_bytes


def test_protected_file_is_not_a_readable_document(protected_docx):
    from docx import Document

    assert is_protected(protected_docx.read_bytes())
    with pytest.raises(Exception):
        Document(str(protected_docx))


def test_roundtrip(protected_docx, nasca):
    res = nasca.decrypt(str(protected_docx))
    assert res.protected
    assert res.data[:2] == b"PK"  # docx는 zip


def test_plain_file_passes_through(tmp_path, nasca):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello")
    res = nasca.decrypt(str(p))
    assert (res.protected, res.data) == (False, b"hello")


def test_protect_refuses_overwrite(tmp_path, nasca):
    dest = tmp_path / "o.bin"
    nasca.protect(b"x", str(dest))
    with pytest.raises(WorkbenchError, match="이미 있습니다"):
        nasca.protect(b"y", str(dest))
    nasca.protect(b"y", str(dest), overwrite=True)
    assert unprotect_bytes(dest.read_bytes()) == b"y"


def test_wrong_key_does_not_recover():
    raw = protect_bytes(b"secret", b"k1")
    assert unprotect_bytes(raw, b"k2") != b"secret"
