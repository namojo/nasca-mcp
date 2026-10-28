"""로컬 하위 프로세스 백엔드. 개발과 테스트 전용이며 격리가 없다.

에이전트를 같은 PC의 별도 프로세스로 띄우고 임시 폴더를 작업 루트로 쓴다.
Windows Sandbox를 쓸 수 없는 환경(리눅스 CI, 클라우드 세션)에서 전체 흐름을 시험하는 용도다.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import workbench

from ...errors import WorkbenchError
from .base import Endpoint


class LocalBackend:
    isolation = "없음(개발용 로컬 프로세스). 실제 보안 경계가 아닙니다."

    def __init__(self, settings=None):
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self.root: str | None = None

    def start(self, token: str, *, session_id: str, lease_s: float, max_ttl_s: float) -> Endpoint:
        self.root = tempfile.mkdtemp(prefix="wb-")
        pkg_parent = str(Path(workbench.__file__).resolve().parents[1])
        env = dict(os.environ)
        env["WORKBENCH_TOKEN"] = token
        env["PYTHONPATH"] = pkg_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = [sys.executable, "-m", "workbench.agent", "--root", self.root, "--lease-s", str(lease_s),
               "--max-ttl-s", str(max_ttl_s)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
        q: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: q.put(self.proc.stdout.readline()), daemon=True).start()
        try:
            line = q.get(timeout=30)
        except queue.Empty:
            self.stop()
            raise WorkbenchError("로컬 에이전트가 30초 안에 시작되지 않았습니다.", code="start_failed") from None
        try:
            info = json.loads(line)
            return Endpoint(info["host"], int(info["port"]))
        except (ValueError, KeyError):
            self.stop()
            raise WorkbenchError("로컬 에이전트 시작 응답을 해석하지 못했습니다.", code="start_failed") from None

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self.proc = None
        if self.root:
            shutil.rmtree(self.root, ignore_errors=True)
            self.root = None
