"""Windows Sandbox 백엔드.

구조
- 호스트: 세션 폴더(control)에 토큰과 설정을 쓰고 .wsb로 샌드박스를 띄운다. control에는 평문이 없다.
- 게스트: 읽기 전용으로 붙은 런타임(내장 파이썬 + 라이브러리 + 에이전트)이 start.ps1로 방화벽을 잠근 뒤
  에이전트를 연다. 에이전트는 호스트 게이트웨이 주소에서 오는 연결만 받는다.
- 평문은 호스트와 게스트 사이 TCP(Hyper-V 가상 스위치)로만 오가고 호스트 디스크에는 닿지 않는다.
- 세션이 끝나면 게스트가 스스로 종료한다. 샌드박스 가상 디스크는 종료와 함께 폐기된다.

주의: Windows Sandbox는 호스트당 인스턴스를 하나만 허용한다. 따라서 작업구역 세션도 하나뿐이다.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from xml.sax.saxutils import escape

from ...errors import WorkbenchError
from .base import Endpoint

SANDBOX_EXE = r"C:\Windows\System32\WindowsSandbox.exe"
GUEST_RUNTIME = r"C:\wbruntime"
GUEST_CONTROL = r"C:\wbcontrol"


def build_wsb(runtime_dir: str, control_dir: str, *, memory_mb: int = 4096) -> str:
    """.wsb 설정 XML. 클립보드·프린터·오디오·비디오·GPU를 끄고, 런타임은 읽기 전용으로 붙인다."""
    logon = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {GUEST_RUNTIME}\\start.ps1"
    return f"""<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Enable</Networking>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <ProtectedClient>Enable</ProtectedClient>
  <MemoryInMB>{int(memory_mb)}</MemoryInMB>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>{escape(runtime_dir)}</HostFolder>
      <SandboxFolder>{GUEST_RUNTIME}</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>{escape(control_dir)}</HostFolder>
      <SandboxFolder>{GUEST_CONTROL}</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>{escape(logon)}</Command>
  </LogonCommand>
</Configuration>
"""


def check_environment(runtime_dir: Path) -> list[tuple[str, str]]:
    """사전 점검. (수준, 메시지) 목록. 수준은 error | warn | ok."""
    out: list[tuple[str, str]] = []
    if os.name != "nt":
        out.append(("error", "Windows가 아닙니다. Windows Sandbox 백엔드는 Windows에서만 동작합니다."))
        return out
    out.append(("ok", "Windows 확인"))
    if not Path(SANDBOX_EXE).exists():
        out.append(("error", "WindowsSandbox.exe가 없습니다. Windows 기능 'Windows 샌드박스'를 켜고 재부팅하세요"
                             "(Pro/Enterprise/Education 에디션과 가상화 지원이 필요합니다)."))
    else:
        out.append(("ok", "WindowsSandbox.exe 확인"))
    need = [runtime_dir / "python" / "python.exe", runtime_dir / "start.ps1",
            runtime_dir / "site-packages" / "workbench" / "agent" / "server.py"]
    for p in need:
        out.append(("ok" if p.exists() else "error",
                    f"런타임 파일 {'확인' if p.exists() else '없음'}: {p}"
                    + ("" if p.exists() else " (scripts/build_runtime.ps1을 실행하세요)")))
    return out


class WinSandboxBackend:
    isolation = "Windows Sandbox(Hyper-V 격리 VM, 클립보드·공유 폴더 평문 없음, 아웃바운드 차단, 종료 시 디스크 폐기)"

    def __init__(self, settings):
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self.session_dir: Path | None = None

    def start(self, token: str, *, session_id: str, lease_s: float, max_ttl_s: float) -> Endpoint:
        problems = [m for lvl, m in check_environment(self.settings.runtime_dir) if lvl == "error"]
        if problems:
            raise WorkbenchError("Windows Sandbox를 시작할 수 없습니다: " + " / ".join(problems), code="start_failed")
        self.session_dir = self.settings.sessions_dir / session_id
        control = self.session_dir / "control"
        control.mkdir(parents=True, exist_ok=True)
        port = 20000 + secrets.randbelow(40000)
        (control / "token.txt").write_text(token, encoding="utf-8")
        (control / "session.json").write_text(
            json.dumps({"port": port, "lease_s": lease_s, "max_ttl_s": max_ttl_s}), encoding="utf-8")
        wsb = self.session_dir / "session.wsb"
        wsb.write_text(build_wsb(str(self.settings.runtime_dir), str(control), memory_mb=self.settings.memory_mb),
                       encoding="utf-8")
        self.proc = subprocess.Popen([SANDBOX_EXE, str(wsb)])
        handshake = control / "agent.json"
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if handshake.exists():
                try:
                    info = json.loads(handshake.read_text(encoding="utf-8"))
                    return Endpoint(info["host"], int(info["port"]))
                except (ValueError, KeyError):
                    pass  # 쓰는 도중일 수 있다
            if self.proc.poll() is not None and not handshake.exists():
                self._cleanup()
                raise WorkbenchError(
                    "샌드박스가 시작 직후 종료됐습니다. 이미 다른 Windows Sandbox가 실행 중인지, "
                    "기능이 켜져 있는지 확인하세요(호스트당 한 개만 실행됩니다).", code="start_failed")
            time.sleep(1)
        log = control / "guest.log"
        detail = log.read_text(encoding="utf-8", errors="replace")[-500:] if log.exists() else "게스트 로그 없음"
        self.stop(force=True)
        raise WorkbenchError(f"샌드박스 에이전트가 5분 안에 준비되지 않았습니다. 게스트 로그: {detail}", code="start_failed")

    def stop(self, force: bool = False) -> None:
        """샌드박스를 내린다. 우리가 띄운 프로세스가 아직 살아 있을 때만 강제 종료한다.

        호스트당 샌드박스는 하나라서, 이미 다른 샌드박스가 떠 있으면 우리가 띄운 프로세스는 곧바로 끝난다.
        그때 이름으로 강제 종료하면 다른 사람이 쓰던 샌드박스를 죽이게 되므로 아무것도 죽이지 않는다.
        """
        if self.proc is not None:
            if not force:
                try:
                    self.proc.wait(timeout=60)  # 게스트가 스스로 종료한다
                except subprocess.TimeoutExpired:
                    pass
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    for exe in ("WindowsSandbox.exe", "WindowsSandboxClient.exe", "WindowsSandboxRemoteSession.exe"):
                        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, check=False)
            self.proc = None
        self._cleanup()

    def _cleanup(self) -> None:
        if self.session_dir is not None:
            shutil.rmtree(self.session_dir, ignore_errors=True)
            self.session_dir = None
