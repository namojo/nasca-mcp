"""NASCA 어댑터. 복호화와 재보호를 하나의 인터페이스 뒤에 숨긴다.

작업구역은 NASCA 내부를 모른다. 사내 NASCA MCP(또는 이후 정식 연동 경로)를 이 인터페이스에 맞춰 꽂는다.
"""

from .base import DecryptResult, NascaAdapter  # noqa: F401
