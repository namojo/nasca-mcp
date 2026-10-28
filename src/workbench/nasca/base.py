from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class DecryptResult:
    data: bytes
    protected: bool          # 원본이 DRM으로 보호돼 있었는가(보호가 아니면 그대로 통과)
    label: str | None = None  # 문서 등급 등 어댑터가 알 수 있는 표시


class NascaAdapter(Protocol):
    name: str

    def decrypt(self, path: str) -> DecryptResult:
        """호스트의 보호 문서를 공식 경로로 복호화해 바이트를 돌려준다."""

    def protect(self, data: bytes, dest_path: str, *, overwrite: bool = False) -> str:
        """바이트를 DRM으로 보호해 dest_path에 저장하고, 실제로 저장된 경로를 돌려준다."""

    def close(self) -> None:
        """연결 등 자원 정리."""
