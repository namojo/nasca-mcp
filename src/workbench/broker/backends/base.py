from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Endpoint:
    host: str
    port: int


class Backend(Protocol):
    isolation: str  # 모델과 사용자에게 보여 줄 격리 수준 설명

    def start(self, token: str, *, session_id: str, lease_s: float, max_ttl_s: float) -> Endpoint:
        """격리 환경을 띄우고 안에서 에이전트를 시작해 접속 정보를 돌려준다."""

    def stop(self) -> None:
        """에이전트가 이미 평문을 지운 뒤 격리 환경을 내리고 호스트 쪽 흔적을 정리한다."""
