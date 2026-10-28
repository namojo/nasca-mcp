import xml.etree.ElementTree as ET
from pathlib import Path

from workbench.broker.backends.winsandbox import build_wsb, check_environment


def test_wsb_is_valid_xml_and_locked_down():
    root = ET.fromstring(build_wsb(r"C:\rt & <x>", r"C:\ctl", memory_mb=2048))
    get = lambda tag: root.find(tag).text
    assert get("ClipboardRedirection") == "Disable"
    assert get("PrinterRedirection") == "Disable"
    assert get("AudioInput") == "Disable" and get("VideoInput") == "Disable" and get("VGpu") == "Disable"
    assert get("MemoryInMB") == "2048"
    folders = {f.find("SandboxFolder").text: f for f in root.iter("MappedFolder")}
    assert folders[r"C:\wbruntime"].find("ReadOnly").text == "true", "런타임은 읽기 전용이어야 한다"
    assert folders[r"C:\wbruntime"].find("HostFolder").text == r"C:\rt & <x>", "특수문자가 이스케이프돼야 한다"
    assert "start.ps1" in root.find("LogonCommand/Command").text


def test_wsb_has_no_plaintext_exchange_folder():
    """평문이 호스트 폴더를 거치지 않는다: 공유 폴더는 런타임(읽기 전용)과 제어(토큰·접속 정보)뿐이다."""
    root = ET.fromstring(build_wsb("a", "b"))
    assert [f.find("SandboxFolder").text for f in root.iter("MappedFolder")] == [r"C:\wbruntime", r"C:\wbcontrol"]


def test_environment_check_explains_on_non_windows(tmp_path):
    res = check_environment(tmp_path)
    assert res and res[0][0] == "error" and "Windows" in res[0][1]


def test_start_ps1_locks_firewall_before_starting_agent():
    ps1 = (Path(__file__).parents[1] / "runtime" / "start.ps1").read_text(encoding="utf-8")
    assert ps1.index("DefaultOutboundAction Block") < ps1.index("workbench.agent")
    assert "-RemoteAddress $gw" in ps1 and "--vm-shutdown" in ps1


class FakeProc:
    def __init__(self, exited: bool):
        self.exited, self.terminated = exited, False

    def poll(self):
        return 0 if (self.exited or self.terminated) else None

    def wait(self, timeout=None):
        if not (self.exited or self.terminated):
            import subprocess
            raise subprocess.TimeoutExpired("x", timeout)

    def terminate(self):
        self.terminated = True


def test_stop_never_kills_other_sandboxes_when_ours_already_exited(tmp_path, monkeypatch):
    """다른 샌드박스가 이미 떠 있어 우리 프로세스가 바로 끝난 경우, 이름으로 강제 종료하지 않는다."""
    from types import SimpleNamespace

    from workbench.broker.backends import winsandbox as ws

    calls = []
    monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: calls.append(a))
    b = ws.WinSandboxBackend(SimpleNamespace(sessions_dir=tmp_path, runtime_dir=tmp_path))
    b.proc = FakeProc(exited=True)
    b.session_dir = tmp_path / "s"
    b.session_dir.mkdir()
    b.stop(force=True)
    assert calls == [] and not b.session_dir


def test_stop_terminates_only_our_own_process(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from workbench.broker.backends import winsandbox as ws

    calls = []
    monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: calls.append(a))
    b = ws.WinSandboxBackend(SimpleNamespace(sessions_dir=tmp_path, runtime_dir=tmp_path))
    proc = FakeProc(exited=False)
    b.proc = proc
    b.stop(force=True)
    assert proc.terminated and calls == [], "우리 프로세스를 정상 종료하면 이름 기반 강제 종료는 필요 없다"
