"""모의 NASCA. 개발과 테스트 전용이며 보안 기능이 없다.

보호 파일은 매직 헤더 + 키 스트림으로 가린 바이트다. 일반 읽기로는 문서로 열리지 않으므로,
"보호 파일은 복호화 경로를 거쳐야 읽힌다"는 흐름을 실제 NASCA 없이 시험할 수 있다.
사용: python -m workbench.nasca.mock protect SRC DST / unprotect SRC DST
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from ..errors import WorkbenchError
from .base import DecryptResult

MAGIC = b"MOCKNASCA\x01"
DEFAULT_KEY = b"workbench-mock-key"


def _stream(key: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data: bytes, key: bytes) -> bytes:
    ks = _stream(key, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def protect_bytes(data: bytes, key: bytes = DEFAULT_KEY) -> bytes:
    return MAGIC + _xor(data, key)


def is_protected(raw: bytes) -> bool:
    return raw.startswith(MAGIC)


def unprotect_bytes(raw: bytes, key: bytes = DEFAULT_KEY) -> bytes:
    if not is_protected(raw):
        raise WorkbenchError("모의 NASCA 보호 파일이 아닙니다.", code="not_protected")
    return _xor(raw[len(MAGIC):], key)


class MockNasca:
    name = "mock"

    def __init__(self, key: bytes | None = None):
        self.key = key or os.environ.get("WORKBENCH_MOCK_NASCA_KEY", "").encode() or DEFAULT_KEY

    def decrypt(self, path: str) -> DecryptResult:
        p = Path(path)
        if not p.is_file():
            raise WorkbenchError(f"파일을 찾을 수 없습니다: {path}", code="not_found")
        raw = p.read_bytes()
        if is_protected(raw):
            return DecryptResult(unprotect_bytes(raw, self.key), protected=True, label="mock")
        return DecryptResult(raw, protected=False)

    def protect(self, data: bytes, dest_path: str, *, overwrite: bool = False) -> str:
        dest = Path(dest_path)
        if dest.exists() and not overwrite:
            raise WorkbenchError(f"'{dest_path}'가 이미 있습니다. overwrite=true로 덮어쓰거나 다른 경로를 쓰세요.",
                                 code="exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(protect_bytes(data, self.key))
        os.replace(tmp, dest)
        return str(dest)

    def close(self) -> None:
        pass


def _cli(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("protect", "unprotect"):
        print("사용: python -m workbench.nasca.mock protect|unprotect SRC DST", file=sys.stderr)
        return 2
    mode, src, dst = argv
    n = MockNasca()
    data = Path(src).read_bytes()
    Path(dst).write_bytes(protect_bytes(data, n.key) if mode == "protect" else unprotect_bytes(data, n.key))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
