"""감사 로그. 내용은 남기지 않고, 누가 무엇을 했는지만 해시 체인으로 남긴다."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

_MAX_STR = 400


def _clean(v):
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + "…"
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v[:50]]
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in list(v.items())[:50]}
    return str(type(v).__name__)


def _digest(prev: str, body: str) -> str:
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path, session_id: str):
        self.path = Path(path)
        self.session_id = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prev = "0" * 64
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        with self._lock:
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": self.session_id, "event": event,
                   "user": os.environ.get("USERNAME") or os.environ.get("USER") or "?", **_clean(fields)}
            body = json.dumps(rec, ensure_ascii=False, sort_keys=True)
            h = _digest(self._prev, body)
            line = json.dumps({"rec": rec, "prev": self._prev, "hash": h}, ensure_ascii=False, sort_keys=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._prev = h


def verify(path: Path) -> tuple[bool, int | None]:
    """체인이 온전한지 확인한다. 깨졌다면 (False, 첫 불일치 줄 번호)."""
    prev = "0" * 64
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        try:
            obj = json.loads(line)
            body = json.dumps(obj["rec"], ensure_ascii=False, sort_keys=True)
            if obj["prev"] != prev or obj["hash"] != _digest(prev, body):
                return False, n
            prev = obj["hash"]
        except (ValueError, KeyError):
            return False, n
    return True, None


def read_events(path: Path) -> list[dict]:
    return [json.loads(l)["rec"] for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
