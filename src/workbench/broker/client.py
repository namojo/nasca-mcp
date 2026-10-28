"""에이전트 HTTP 클라이언트(호스트 쪽). 표준 라이브러리만 쓴다."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from ..errors import WorkbenchError


class AgentClient:
    def __init__(self, host: str, port: int, token: str, *, timeout: float = 120.0):
        self.base = f"http://{host}:{port}"
        self.token = token
        self.timeout = timeout

    def call(self, method: str, timeout: float | None = None, **params) -> dict:
        body = json.dumps({"method": method, "params": params}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/rpc", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                obj = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                obj = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise WorkbenchError(f"작업구역 응답 오류(HTTP {e.code})", code="session_lost") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            raise WorkbenchError(
                "작업구역과 연결이 끊겼습니다. 세션이 종료됐을 수 있습니다. workbench_status로 확인하세요.",
                code="session_lost") from None
        if not obj.get("ok"):
            err = obj.get("error", {})
            raise WorkbenchError(err.get("message", "알 수 없는 오류"), code=err.get("code", "error"))
        return obj["result"]

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=3) as r:
                    if json.loads(r.read().decode("utf-8")).get("ok"):
                        return
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(0.5)
        raise WorkbenchError(f"작업구역 에이전트가 응답하지 않습니다({type(last).__name__}).", code="session_lost")


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s)
