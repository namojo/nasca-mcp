#!/usr/bin/env python3
"""플러그인이 MCP 서버를 띄우는 진입점. 표준 라이브러리만 쓴다.

설치 스크립트가 만든 가상환경(의존성이 들어 있음)이 있으면 그 파이썬으로, 없으면 지금 파이썬으로 서버를 실행한다.
가상환경 위치는 NASCA_MCP_PYTHON 환경변수로 바꿀 수 있다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def candidates() -> list[Path]:
    out: list[Path] = []
    if os.environ.get("NASCA_MCP_PYTHON"):
        out.append(Path(os.environ["NASCA_MCP_PYTHON"]))
    if os.environ.get("LOCALAPPDATA"):
        out.append(Path(os.environ["LOCALAPPDATA"]) / "nasca-mcp" / "venv" / "Scripts" / "python.exe")
    out.append(Path.home() / ".nasca-mcp" / "venv" / "bin" / "python")
    return out


def pick_python() -> str:
    for c in candidates():
        if c.is_file():
            return str(c)
    return sys.executable


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # exec 대신 자식 프로세스로 실행한다. Windows의 os.exec*는 부모가 먼저 끝나 stdio 연결이 끊긴다.
    return subprocess.call([pick_python(), "-m", "workbench.mcp_server"], env=env)


if __name__ == "__main__":
    sys.exit(main())
