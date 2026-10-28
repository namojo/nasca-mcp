from __future__ import annotations

import json
import os
from pathlib import Path

from ..errors import WorkbenchError
from .base import NascaAdapter


def build_nasca(kind: str | None = None, config_path: str | None = None) -> NascaAdapter:
    kind = (kind or os.environ.get("WORKBENCH_NASCA", "mock")).lower()
    if kind == "mock":
        from .mock import MockNasca
        return MockNasca()
    if kind == "mcp":
        from .mcp_bridge import McpNasca
        path = config_path or os.environ.get("WORKBENCH_NASCA_MCP_CONFIG")
        if not path:
            raise WorkbenchError("WORKBENCH_NASCA_MCP_CONFIG에 NASCA MCP 설정 파일 경로를 지정하세요.", code="bad_config")
        return McpNasca(json.loads(Path(path).read_text(encoding="utf-8")))
    raise WorkbenchError(f"알 수 없는 NASCA 어댑터: {kind} (mock | mcp)", code="bad_config")
