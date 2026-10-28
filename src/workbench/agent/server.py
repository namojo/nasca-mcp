"""격리 환경 안에서 도는 HTTP 에이전트.

- 인증: 세션 토큰(Bearer). 호스트의 중개 계층만 안다.
- 수명: 중개 계층의 연락이 lease_s 동안 끊기거나 max_ttl을 넘으면 스스로 평문을 지우고 끝낸다(데드맨 스위치).
- 로그: 메서드 이름과 결과 코드만 남긴다. 파일 이름과 내용은 남기지 않는다.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..errors import WorkbenchError
from .rpc import Dispatcher
from .store import Store


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # 패킷은 나가지 않는다. 나가는 인터페이스의 주소만 알아낸다.
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


class AgentServer:
    def __init__(self, root, token: str, *, host: str = "127.0.0.1", port: int = 0, lease_s: float = 120,
                 max_ttl_s: float = 4 * 3600, max_body: int = 400 * 1024 * 1024, on_expire=None):
        if not token or len(token) < 16:
            raise ValueError("토큰이 너무 짧습니다.")
        self.token = token
        self.store = Store(root)
        self.dispatcher = Dispatcher(self.store)
        self.lease_s = lease_s
        self.max_ttl_s = max_ttl_s
        self.max_body = max_body
        self.on_expire = on_expire
        self.started = time.monotonic()
        self.last_contact = time.monotonic()
        self._rpc_lock = threading.Lock()
        self._stop = threading.Event()
        self._expired = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, fmt, *args):  # 요청 줄에 이름이 들어갈 수 있어 기본 로그를 끈다
                pass

            def _send(self, code: int, obj: dict) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                got = self.headers.get("Authorization", "")
                want = f"Bearer {outer.token}"
                return hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"ok": False, "error": {"code": "not_found", "message": "없는 경로"}})

            def do_POST(self):
                if self.path != "/rpc":
                    return self._send(404, {"ok": False, "error": {"code": "not_found", "message": "없는 경로"}})
                if not self._authed():
                    return self._send(401, {"ok": False, "error": {"code": "unauthorized", "message": "인증 실패"}})
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    length = -1
                if length < 0:
                    return self._send(411, {"ok": False, "error": {"code": "bad_request", "message": "Content-Length 필요"}})
                if length > outer.max_body:
                    return self._send(413, {"ok": False, "error": {"code": "too_large", "message": "요청이 너무 큽니다"}})
                outer.last_contact = time.monotonic()
                try:
                    req = json.loads(self.rfile.read(length).decode("utf-8"))
                    method, params = req["method"], req.get("params") or {}
                except Exception:
                    return self._send(400, {"ok": False, "error": {"code": "bad_request", "message": "JSON 해석 실패"}})
                if method == "shutdown":
                    with outer._rpc_lock:
                        wiped = outer.store.wipe()
                    self._send(200, {"ok": True, "result": {"wiped": wiped}})
                    threading.Thread(target=outer.expire, daemon=True).start()
                    return
                try:
                    with outer._rpc_lock:
                        result = outer.dispatcher.call(method, params)
                    self._send(200, {"ok": True, "result": result})
                except WorkbenchError as e:
                    self._send(200, {"ok": False, "error": {"code": e.code, "message": str(e)}})
                except TypeError:
                    self._send(200, {"ok": False, "error": {"code": "bad_params", "message": f"{method}: 인자가 맞지 않습니다."}})
                except Exception as e:  # 라이브러리 예외 문구에 내용 조각이 있을 수 있어 형식만 알린다
                    self._send(200, {"ok": False, "error": {"code": "internal", "message": f"내부 오류({type(e).__name__})"}})

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[0], self.httpd.server_address[1]
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        t = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        w = threading.Thread(target=self._watchdog, daemon=True)
        t.start()
        w.start()
        self._threads = [t, w]

    def _watchdog(self) -> None:
        while not self._stop.wait(0.5):
            now = time.monotonic()
            if now - self.last_contact > self.lease_s or now - self.started > self.max_ttl_s:
                self.expire()
                return

    def expire(self) -> None:
        if self._expired:
            return
        self._expired = True
        self.close()
        if self.on_expire:
            self.on_expire()

    def close(self) -> None:
        """평문을 지우고 서버를 멈춘다."""
        self._stop.set()
        with self._rpc_lock:
            self.store.wipe()
        self.httpd.shutdown()
        self.httpd.server_close()


def _vm_shutdown() -> None:
    if os.name == "nt":
        subprocess.run(["shutdown", "/s", "/t", "0", "/f"], check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="workbench-agent")
    ap.add_argument("--root", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token-file", help="토큰이 든 파일. 없으면 환경변수 WORKBENCH_TOKEN")
    ap.add_argument("--handshake-file", help="준비되면 접속 정보를 이 파일에 쓴다")
    ap.add_argument("--lease-s", type=float, default=120)
    ap.add_argument("--max-ttl-s", type=float, default=4 * 3600)
    ap.add_argument("--vm-shutdown", action="store_true", help="종료할 때 격리 환경(VM)도 끈다")
    a = ap.parse_args(argv)

    token = Path(a.token_file).read_text(encoding="utf-8").strip() if a.token_file else os.environ.get("WORKBENCH_TOKEN", "")
    done = threading.Event()

    def finish():
        if a.vm_shutdown:
            _vm_shutdown()
        done.set()

    srv = AgentServer(a.root, token, host=a.host, port=a.port, lease_s=a.lease_s, max_ttl_s=a.max_ttl_s,
                      on_expire=finish)
    advertised = local_ip() if a.host in ("0.0.0.0", "::") else srv.host
    info = {"event": "ready", "host": advertised, "port": srv.port, "pid": os.getpid()}
    srv.start()
    line = json.dumps(info)
    if a.handshake_file:
        tmp = a.handshake_file + ".tmp"
        Path(tmp).write_text(line, encoding="utf-8")
        os.replace(tmp, a.handshake_file)
    print(line, flush=True)
    done.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
