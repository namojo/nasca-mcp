"""기존 NASCA MCP 서버를 어댑터로 감싼다.

작업구역은 NASCA MCP를 AI에게 노출하지 않고 중개 계층만 호출한다. 도구 이름과 인자, 결과 모양은
설정 파일로 맞춘다. 사내 NASCA MCP의 실제 계약은 이 저장소가 알 수 없으므로, 아래 예시를 실제 값으로 고쳐 쓴다.

설정 예(JSON):
{
  "command": "node", "args": ["C:/tools/nasca-mcp/index.js"], "env": {},
  "call_timeout_s": 120,
  "decrypt": {
    "tool": "nasca_decrypt",
    "args": {"path": "{path}"},
    "result": {"mode": "base64", "field": "content_base64", "label_field": "grade"}
  },
  "protect": {
    "tool": "nasca_protect",
    "input": "base64",
    "args": {"path": "{dest}", "content_base64": "{data_b64}", "overwrite": "{overwrite}"},
    "result": {"path_field": "path"}
  }
}
result.mode: base64(필드에 base64) | text(응답 본문이 곧 파일) | file(필드에 복호화된 파일 경로).
file 모드는 평문이 호스트 디스크를 거치므로 delete_after로 읽은 즉시 덮어써 지운다. 가능하면 base64를 쓴다.
protect.input: base64 | file(평문을 임시 파일에 쓰고 {src}로 넘긴 뒤 즉시 지운다).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from .._mcpcompat import result_is_error
from ..errors import WorkbenchError
from .base import DecryptResult


def _fill(obj, values: dict):
    if isinstance(obj, str):
        if obj.startswith("{") and obj.endswith("}") and obj[1:-1] in values:
            return values[obj[1:-1]]  # 문자열이 아닌 값(불리언 등)은 형을 유지한다
        for k, v in values.items():
            obj = obj.replace("{" + k + "}", str(v))
        return obj
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    return obj


def _payload(result):
    if result_is_error(result):
        texts = [getattr(b, "text", "") for b in result.content]
        raise WorkbenchError("NASCA MCP가 오류를 돌려줬습니다: " + (" ".join(texts)[:300] or "(메시지 없음)"),
                             code="nasca_error")
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if structured:
        return structured
    text = "\n".join(getattr(b, "text", "") for b in result.content if getattr(b, "type", None) == "text")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _shred_and_remove(p: Path) -> None:
    try:
        size = p.stat().st_size
        with open(p, "r+b", buffering=0) as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    p.unlink(missing_ok=True)


def _stderr_target():
    """자식 프로세스의 stderr 대상. 진짜 파일 기술자가 없는 stderr(캡처 중 등)면 버린다."""
    try:
        sys.stderr.fileno()
        return sys.stderr
    except (AttributeError, OSError, ValueError):
        return open(os.devnull, "w")


class McpNasca:
    name = "mcp"

    def __init__(self, config: dict):
        for key in ("command", "decrypt", "protect"):
            if key not in config:
                raise WorkbenchError(f"NASCA MCP 설정에 '{key}'가 없습니다.", code="bad_config")
        self.cfg = config
        self.timeout = float(config.get("call_timeout_s", 120))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._ready = threading.Event()
        self._closing: asyncio.Event | None = None
        self._start_error: BaseException | None = None
        self._lock = threading.Lock()

    # --- 연결 수명: 하나의 태스크가 컨텍스트를 소유한다 ------------------------
    async def _run(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._closing = asyncio.Event()
        params = StdioServerParameters(command=self.cfg["command"], args=self.cfg.get("args", []),
                                       env=self.cfg.get("env") or None)
        try:
            async with stdio_client(params, errlog=_stderr_target()) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._closing.wait()
        except BaseException as e:  # noqa: BLE001
            self._start_error = e
            self._ready.set()
        finally:
            self._session = None
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)  # 스레드가 끝나도록 루프를 멈춘다

    def _ensure(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            self._ready.clear()
            self._start_error = None
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
            asyncio.run_coroutine_threadsafe(self._run(), self._loop)
            if not self._ready.wait(60) or self._session is None:
                err = type(self._start_error).__name__ if self._start_error else "시간 초과"
                self._teardown()
                raise WorkbenchError(f"NASCA MCP 서버를 시작하지 못했습니다({err}). 설정의 command/args를 확인하세요.",
                                     code="nasca_unavailable")

    def _call(self, tool: str, args: dict):
        self._ensure()
        fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(tool, args), self._loop)
        try:
            return fut.result(self.timeout)
        except TimeoutError:
            fut.cancel()
            raise WorkbenchError(f"NASCA MCP 호출이 {self.timeout:.0f}초 안에 끝나지 않았습니다.", code="nasca_timeout") from None

    # --- 어댑터 인터페이스 ----------------------------------------------------
    def decrypt(self, path: str) -> DecryptResult:
        spec = self.cfg["decrypt"]
        result = self._call(spec["tool"], _fill(spec.get("args", {}), {"path": path}))
        payload = _payload(result)
        rspec = spec.get("result", {"mode": "base64", "field": "content_base64"})
        mode = rspec.get("mode", "base64")
        label = payload.get(rspec["label_field"]) if isinstance(payload, dict) and rspec.get("label_field") else None
        if mode == "text":
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False)
            return DecryptResult(payload.encode("utf-8"), protected=True, label=label)
        if not isinstance(payload, dict) or rspec.get("field") not in payload:
            keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            raise WorkbenchError(f"NASCA MCP 응답에 '{rspec.get('field')}' 필드가 없습니다. 응답 필드: {keys}. "
                                 "설정의 decrypt.result.field를 맞추세요.", code="bad_config")
        value = payload[rspec["field"]]
        if mode == "base64":
            try:
                return DecryptResult(base64.b64decode(value, validate=True), protected=True, label=label)
            except Exception:
                raise WorkbenchError("NASCA MCP 응답의 base64를 해석하지 못했습니다.", code="bad_config") from None
        if mode == "file":
            p = Path(value)
            if not p.is_file():
                raise WorkbenchError("NASCA MCP가 알려 준 복호화 파일 경로를 찾을 수 없습니다.", code="nasca_error")
            data = p.read_bytes()
            if rspec.get("delete_after", True):
                _shred_and_remove(p)
            return DecryptResult(data, protected=True, label=label)
        raise WorkbenchError(f"알 수 없는 decrypt.result.mode: {mode}", code="bad_config")

    def protect(self, data: bytes, dest_path: str, *, overwrite: bool = False) -> str:
        spec = self.cfg["protect"]
        values = {"dest": dest_path, "overwrite": overwrite}
        tmp: Path | None = None
        if spec.get("input", "base64") == "file":
            fd, name = tempfile.mkstemp(prefix=".wb-", dir=self.cfg.get("temp_dir"))
            os.close(fd)
            tmp = Path(name)
            tmp.write_bytes(data)
            values["src"] = str(tmp)
        else:
            values["data_b64"] = base64.b64encode(data).decode("ascii")
        try:
            result = self._call(spec["tool"], _fill(spec.get("args", {}), values))
            payload = _payload(result)
        finally:
            if tmp is not None:
                _shred_and_remove(tmp)
        field = (spec.get("result") or {}).get("path_field")
        if field and isinstance(payload, dict) and payload.get(field):
            return str(payload[field])
        return dest_path

    def list_tools(self) -> list[dict]:
        """NASCA MCP가 제공하는 도구 이름과 인자 이름. 설정 파일을 실제 계약에 맞출 때 쓴다."""
        self._ensure()
        fut = asyncio.run_coroutine_threadsafe(self._session.list_tools(), self._loop)
        res = fut.result(self.timeout)
        out = []
        for t in res.tools:
            schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}
            out.append({"name": t.name, "args": sorted((schema.get("properties") or {}).keys()),
                        "description": (t.description or "")[:100]})
        return out

    def _teardown(self) -> None:
        if self._loop is not None and self._closing is not None:
            self._loop.call_soon_threadsafe(self._closing.set)
        self._session = None

    def close(self) -> None:
        with self._lock:
            if self._loop is not None and self._closing is not None:
                self._loop.call_soon_threadsafe(self._closing.set)
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._session = None
            self._loop = None
            self._thread = None
