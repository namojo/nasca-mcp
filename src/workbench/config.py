"""설정. 우선순위는 기본값 < 사용자 설정 파일 < 환경변수 < 기계 설정 파일이다.

기계 설정 파일(Windows는 %ProgramData%\\workbench\\config.json, 그 밖은 /etc/workbench/config.json)은
관리자만 쓸 수 있는 위치에 둔다. 거기 적힌 값은 사용자와 환경변수가 바꾸지 못한다.
열 수 있는 폴더, 내보낼 수 있는 폴더, 백엔드, NASCA 어댑터 같은 보안 값을 IT가 고정하는 용도다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import WorkbenchError


def default_home(env=os.environ) -> Path:
    if env.get("WORKBENCH_HOME"):
        return Path(env["WORKBENCH_HOME"])
    base = env.get("LOCALAPPDATA") if os.name == "nt" else None
    return Path(base) / "workbench" if base else Path.home() / ".workbench"


def machine_config_path(env=os.environ) -> Path:
    if os.name == "nt":
        return Path(env.get("PROGRAMDATA", r"C:\ProgramData")) / "workbench" / "config.json"
    return Path("/etc/workbench/config.json")


@dataclass
class Settings:
    home: Path = field(default_factory=default_home)
    backend: str = "winsandbox" if os.name == "nt" else "local"
    nasca: str = "mock"
    nasca_mcp_config: Path | None = None
    idle_ttl_s: float = 900.0        # 사용자가 아무 작업도 안 하면 이 시간 뒤 평문을 지운다
    max_ttl_s: float = 4 * 3600.0    # 한 세션의 절대 수명
    lease_s: float = 120.0           # 중개 계층 연락이 이만큼 끊기면 에이전트가 스스로 지운다
    open_roots: list[Path] = field(default_factory=list)    # 비어 있으면 제한 없음
    export_roots: list[Path] = field(default_factory=list)  # 비어 있으면 제한 없음
    runtime_dir: Path | None = None  # Windows Sandbox에 읽기 전용으로 붙일 런타임
    memory_mb: int = 4096
    max_open_bytes: int = 200 * 1024 * 1024
    max_files: int = 100

    # 설정 이름 → 변환 함수
    _CONVERT = {
        "backend": lambda v: str(v).lower(), "nasca": lambda v: str(v).lower(),
        "nasca_mcp_config": lambda v: Path(v) if v else None,
        "idle_ttl_s": float, "max_ttl_s": float, "lease_s": float,
        "open_roots": lambda v: [Path(p).expanduser() for p in v],
        "export_roots": lambda v: [Path(p).expanduser() for p in v],
        "runtime_dir": lambda v: Path(v) if v else None,
        "memory_mb": int, "max_open_bytes": int, "max_files": int,
    }

    def _apply(self, layer: dict, source: str) -> None:
        for key, raw in layer.items():
            conv = self._CONVERT.get(key)
            if conv is None:
                continue  # 모르는 키는 무시한다
            try:
                setattr(self, key, conv(raw))
            except (TypeError, ValueError):
                raise WorkbenchError(f"{source}의 '{key}' 값이 올바르지 않습니다: {raw!r}", code="bad_config") from None

    @classmethod
    def load(cls, env=None, machine_config: Path | None = None) -> "Settings":
        env = os.environ if env is None else env
        s = cls(home=default_home(env))
        s.runtime_dir = s.home / "runtime"
        for path, label in ((s.home / "config.json", "사용자 설정"), ):
            if path.is_file():
                s._apply(_read_json(path), f"{label}({path})")
        layer: dict = {}
        for key in cls._CONVERT:
            val = env.get(f"WORKBENCH_{key.upper()}")
            if val is None:
                continue
            layer[key] = val.split(os.pathsep) if key in ("open_roots", "export_roots") and val.strip() else \
                ([] if key in ("open_roots", "export_roots") else val)
        if "WORKBENCH_NASCA_MCP_CONFIG" in env:
            layer["nasca_mcp_config"] = env["WORKBENCH_NASCA_MCP_CONFIG"]
        s._apply(layer, "환경변수")
        mpath = machine_config if machine_config is not None else machine_config_path(env)
        if mpath.is_file():
            s._apply(_read_json(mpath), f"기계 설정({mpath})")
        return s

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.load()

    @property
    def audit_dir(self) -> Path:
        return self.home / "audit"

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"


def _read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(obj, dict):
            raise ValueError("최상위가 객체가 아님")
        return obj
    except (OSError, ValueError) as e:
        raise WorkbenchError(f"설정 파일 {path}를 읽을 수 없습니다({type(e).__name__}).", code="bad_config") from None


def is_within(path: Path, roots: list[Path]) -> bool:
    if not roots:
        return True
    p = os.path.normcase(str(path.resolve()))
    for r in roots:
        rr = os.path.normcase(str(r.resolve()))
        if p == rr or p.startswith(rr.rstrip("\\/") + os.sep):
            return True
    return False
