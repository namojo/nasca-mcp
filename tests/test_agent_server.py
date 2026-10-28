import json
import time
import urllib.error
import urllib.request

import pytest

from workbench.agent.server import AgentServer
from workbench.broker.client import AgentClient, b64e
from workbench.errors import WorkbenchError

TOKEN = "t" * 32


@pytest.fixture
def srv(tmp_path):
    s = AgentServer(tmp_path / "root", TOKEN, lease_s=60)
    s.start()
    yield s
    if not s._expired:
        s.close()


def client(s, token=TOKEN):
    return AgentClient(s.host, s.port, token, timeout=10)


def test_rpc_roundtrip(srv):
    c = client(srv)
    c.wait_ready(5)
    c.call("put", name="a.txt", data_b64=b64e("안녕".encode()))
    assert "안녕" in c.call("read", name="a.txt")["text"]


def test_wrong_token_is_rejected(srv):
    with pytest.raises(WorkbenchError) as ei:
        client(srv, "x" * 32).call("list")
    assert ei.value.code == "unauthorized"


def test_no_token_and_other_paths_are_rejected(srv):
    req = urllib.request.Request(f"http://{srv.host}:{srv.port}/rpc", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(f"http://{srv.host}:{srv.port}/files", timeout=5)
    assert ei.value.code == 404


def test_health_reveals_nothing(srv):
    body = json.loads(urllib.request.urlopen(f"http://{srv.host}:{srv.port}/health", timeout=5).read())
    assert body == {"ok": True}


def test_unknown_method_and_bad_params_are_clean_errors(srv):
    c = client(srv)
    with pytest.raises(WorkbenchError, match="알 수 없는 메서드"):
        c.call("os_system")
    with pytest.raises(WorkbenchError) as ei:
        c.call("put", bogus=1)
    assert ei.value.code == "bad_params"


def test_oversized_body_is_refused(tmp_path):
    s = AgentServer(tmp_path / "r", TOKEN, max_body=1000)
    s.start()
    try:
        with pytest.raises(WorkbenchError):
            client(s).call("put", name="a.txt", data_b64="A" * 5000)
    finally:
        s.close()


def test_shutdown_rpc_wipes_and_stops(srv):
    c = client(srv)
    c.call("put", name="a.txt", data_b64=b64e(b"x"))
    c.call("shutdown")
    time.sleep(0.5)
    assert not [p for p in srv.store.root.rglob("*") if p.is_file()]
    with pytest.raises(WorkbenchError) as ei:
        c.call("list")
    assert ei.value.code == "session_lost"


def test_lease_expiry_wipes_plaintext_without_broker(tmp_path):
    """중개 계층이 죽어도 에이전트가 스스로 평문을 지운다."""
    fired = []
    s = AgentServer(tmp_path / "r", TOKEN, lease_s=1.0, on_expire=lambda: fired.append(1))
    s.start()
    c = client(s)
    c.call("put", name="a.txt", data_b64=b64e(b"secret"))
    f = next(s.store.files.rglob("a.txt"))
    assert f.exists()
    time.sleep(3)
    assert fired and not f.exists()


def test_activity_keeps_lease_alive(tmp_path):
    s = AgentServer(tmp_path / "r", TOKEN, lease_s=1.5)
    s.start()
    try:
        c = client(s)
        for _ in range(6):
            c.call("ping")
            time.sleep(0.5)
        assert c.call("ping")["files"] == 0
    finally:
        s.close()


def test_short_token_refused(tmp_path):
    with pytest.raises(ValueError):
        AgentServer(tmp_path / "r", "short")
