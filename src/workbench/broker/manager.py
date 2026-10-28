"""작업구역 세션 관리자. 도구가 부르는 모든 동작이 여기를 지난다."""

from __future__ import annotations

import atexit
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..agent.kinds import kind_of, normalize_name
from ..config import Settings, is_within
from ..errors import WorkbenchError
from ..manifest import RULES
from ..nasca.base import NascaAdapter
from .audit import AuditLog
from .backends.factory import build_backend
from .client import AgentClient, b64d, b64e


@dataclass
class FileRec:
    kind: str
    source: str | None = None
    protected: bool = False
    label: str | None = None
    dirty: bool = False
    derived: bool = False
    exported_to: str | None = None
    source_sig: tuple[int, int] | None = None  # 열 당시 원본의 (수정 시각 ns, 크기). 내보낼 때 충돌을 알아챈다


@dataclass
class Session:
    id: str
    backend: object
    client: AgentClient
    audit: AuditLog
    started: float
    last_activity: float
    files: dict[str, FileRec] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=lambda: dict(decrypts=0, protects=0, reads=0, edits=0))
    stop_monitor: threading.Event = field(default_factory=threading.Event)


def _same_path(a, b) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def _signature(p: Path) -> tuple[int, int] | None:
    try:
        st = Path(p).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


class Manager:
    def __init__(self, settings: Settings, nasca: NascaAdapter, backend_factory=build_backend, clock=time.monotonic):
        self.settings = settings
        self.nasca = nasca
        self.backend_factory = backend_factory
        self.clock = clock
        self.lock = threading.RLock()
        self.session: Session | None = None
        self.last_end: dict | None = None
        atexit.register(self.shutdown)

    # --- 세션 수명 ---------------------------------------------------
    def _start(self) -> Session:
        sid = f"s-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        token = secrets.token_urlsafe(32)
        backend = self.backend_factory(self.settings)
        endpoint = backend.start(token, session_id=sid, lease_s=self.settings.lease_s, max_ttl_s=self.settings.max_ttl_s)
        client = AgentClient(endpoint.host, endpoint.port, token)
        try:
            client.wait_ready(60)
        except WorkbenchError:
            backend.stop()
            raise
        now = self.clock()
        s = Session(id=sid, backend=backend, client=client, started=now, last_activity=now,
                    audit=AuditLog(self.settings.audit_dir / f"{sid}.jsonl", sid))
        s.audit.write("session_start", isolation=backend.isolation, backend=self.settings.backend,
                      nasca=self.nasca.name, idle_ttl_s=self.settings.idle_ttl_s, max_ttl_s=self.settings.max_ttl_s)
        self.session = s
        self.last_end = None
        threading.Thread(target=self._monitor, args=(s,), daemon=True).start()
        return s

    def _monitor(self, s: Session) -> None:
        interval = max(0.2, min(30.0, self.settings.lease_s / 4, self.settings.idle_ttl_s / 4))
        while not s.stop_monitor.wait(interval):
            with self.lock:
                if self.session is not s:
                    return
                now = self.clock()
                if now - s.last_activity > self.settings.idle_ttl_s:
                    self._end(s, "유휴 시간 초과", discard=True)
                    return
                if now - s.started > self.settings.max_ttl_s:
                    self._end(s, "최대 수명 초과", discard=True)
                    return
                try:
                    s.client.call("ping", timeout=10)
                except WorkbenchError:
                    s.audit.write("agent_lost")
                    self._end(s, "작업구역 연결 끊김", discard=True)
                    return

    def _end(self, s: Session, reason: str, *, discard: bool) -> dict:
        dirty = sorted(n for n, f in s.files.items() if f.dirty)
        try:
            s.client.call("shutdown", timeout=30)
        except WorkbenchError:
            pass
        try:
            s.backend.stop()
        finally:
            s.stop_monitor.set()
        summary = {"decrypts": s.counters["decrypts"], "protects": s.counters["protects"],
                   "seconds": int(self.clock() - s.started)}
        s.audit.write("session_end", reason=reason, discarded_unexported=dirty if discard else [], **summary)
        self.last_end = {"reason": reason, "discarded": dirty, **summary}
        self.session = None
        return {"reason": reason, "discarded_unexported": dirty, **summary}

    def shutdown(self) -> None:
        with self.lock:
            if self.session is not None:
                try:
                    self._end(self.session, "서버 종료", discard=True)
                except Exception:  # noqa: BLE001  종료 경로에서는 더 던지지 않는다
                    pass
        try:
            self.nasca.close()
        except Exception:  # noqa: BLE001
            pass

    def _require(self) -> Session:
        s = self.session
        if s is None:
            if self.last_end:
                lost = self.last_end["discarded"]
                extra = f" 저장하지 못한 수정: {', '.join(lost)}." if lost else ""
                raise WorkbenchError(
                    f"작업구역이 '{self.last_end['reason']}'로 종료되어 복호화된 파일이 모두 삭제됐습니다.{extra} "
                    "계속하려면 workbench_open으로 문서를 다시 여세요(새 세션이 시작됩니다).", code="session_ended")
            raise WorkbenchError("열려 있는 작업구역이 없습니다. workbench_open으로 문서를 여세요.", code="no_session")
        s.last_activity = self.clock()
        return s

    def _file(self, s: Session, name: str) -> tuple[str, FileRec]:
        key = normalize_name(name)
        if key not in s.files:
            have = ", ".join(sorted(s.files)) or "(없음)"
            raise WorkbenchError(f"작업구역에 '{key}'가 없습니다. 현재 파일: {have}", code="not_found")
        return key, s.files[key]

    # --- 상태 --------------------------------------------------------
    def brief(self) -> dict:
        """에이전트를 부르지 않는 가벼운 상태. 도구 결과 끝의 한 줄 요약에 쓴다."""
        s = self.session
        if s is None:
            return {"active": False}
        now = self.clock()
        return {"active": True, "files": [{"dirty": f.dirty} for f in s.files.values()],
                "idle_remaining_s": max(0, self.settings.idle_ttl_s - (now - s.last_activity)),
                "lifetime_remaining_s": max(0, self.settings.max_ttl_s - (now - s.started))}

    def status(self) -> dict:
        with self.lock:
            s = self.session
            if s is None:
                return {"active": False, "last_end": self.last_end, "rules": RULES}
            now = self.clock()
            listing = {f["name"]: f for f in s.client.call("list")["files"]}
            files = []
            for name, rec in sorted(s.files.items()):
                info = listing.get(name, {})
                files.append({"name": name, "kind": rec.kind, "size": info.get("size", 0), "dirty": rec.dirty,
                              "protected": rec.protected, "derived": rec.derived, "source": rec.source,
                              "exported_to": rec.exported_to})
            return {
                "active": True, "session": s.id, "isolation": s.backend.isolation, "nasca": self.nasca.name,
                "idle_remaining_s": max(0, self.settings.idle_ttl_s - (now - s.last_activity)),
                "lifetime_remaining_s": max(0, self.settings.max_ttl_s - (now - s.started)),
                "files": files, "counters": dict(s.counters), "rules": RULES,
            }

    # --- 열기: 복호화는 여기서만 일어난다 -------------------------------------
    def open(self, source_path: str, name: str | None = None) -> dict:
        with self.lock:
            src = Path(source_path).expanduser()
            if not is_within(src, self.settings.open_roots):
                raise WorkbenchError("허용된 폴더 밖의 파일입니다. 열 수 있는 폴더: "
                                     + ", ".join(str(r) for r in self.settings.open_roots), code="denied")
            if not src.is_file():
                raise WorkbenchError(f"파일을 찾을 수 없습니다: {source_path}", code="not_found")
            if src.stat().st_size > self.settings.max_open_bytes:
                raise WorkbenchError("파일이 너무 큽니다.", code="too_large")
            key = normalize_name(name or src.name)
            s = self.session or self._start()
            s.last_activity = self.clock()
            if key in s.files:
                raise WorkbenchError(f"작업구역에 '{key}'가 이미 있습니다. 다른 name을 지정하세요.", code="exists")
            for other, rec in s.files.items():
                if rec.source and _same_path(rec.source, src):
                    raise WorkbenchError(
                        f"이 파일은 이미 '{other}'로 열려 있습니다. 사본을 둘로 만들면 수정이 엇갈리므로 "
                        f"'{other}'를 계속 쓰세요.", code="exists")
            if len(s.files) >= self.settings.max_files:
                raise WorkbenchError("작업구역 파일 수 한도에 도달했습니다.", code="too_large")
            res = self.nasca.decrypt(str(src))
            s.counters["decrypts"] += 1
            try:
                put = s.client.call("put", name=key, data_b64=b64e(res.data))
            finally:
                size = len(res.data)
                del res.data
            info = put["info"]
            s.files[key] = FileRec(kind=info["kind"], source=str(src), protected=res.protected, label=res.label,
                                   source_sig=_signature(src))
            s.audit.write("open", name=key, source=str(src), protected=res.protected, label=res.label, size=size,
                          sha256=info["sha256"], kind=info["kind"])
            out = {"name": key, "kind": info["kind"], "size": info["size"], "was_drm_protected": res.protected,
                   "outline": put.get("outline")}
            if "outline_error" in put:
                out["outline_error"] = put["outline_error"]
            if info["kind"] == "binary":
                out["note"] = "읽기·편집을 지원하지 않는 형식이다. workbench_export로 내보내는 것만 가능하다."
            return out

    # --- 읽기 --------------------------------------------------------
    def list_files(self) -> dict:
        with self.lock:
            s = self._require()
            return {"files": s.client.call("list")["files"]}

    def outline(self, name: str) -> dict:
        with self.lock:
            s = self._require()
            key, _ = self._file(s, name)
            out = s.client.call("outline", name=key)
            s.audit.write("outline", name=key)
            return out

    def read(self, name: str, **sel) -> dict:
        with self.lock:
            s = self._require()
            key, _ = self._file(s, name)
            out = s.client.call("read", name=key, **{k: v for k, v in sel.items() if v is not None})
            s.counters["reads"] += 1
            s.audit.write("read", name=key, selector={k: v for k, v in sel.items() if v is not None},
                          chars=len(out.get("text", "")))
            return out

    def view_image(self, name: str, **kw) -> dict:
        with self.lock:
            s = self._require()
            key, _ = self._file(s, name)
            out = s.client.call("image", name=key, **{k: v for k, v in kw.items() if v is not None})
            s.counters["reads"] += 1
            s.audit.write("view_image", name=key, crop=kw.get("crop_px") or kw.get("crop_frac"),
                          width=out["width"], height=out["height"])
            out["data"] = b64d(out.pop("data_b64"))
            return out

    def extract_media(self, name: str, media: list[str] | None = None) -> dict:
        with self.lock:
            s = self._require()
            key, _ = self._file(s, name)
            out = s.client.call("extract_media", name=key, media=media)
            for saved in out["saved"]:
                s.files[saved] = FileRec(kind=kind_of(saved), derived=True, source=f"{key}에서 추출")
            s.audit.write("extract_media", name=key, saved=out["saved"])
            return out

    # --- 쓰기 --------------------------------------------------------
    def edit(self, name: str, ops: list[dict]) -> dict:
        with self.lock:
            s = self._require()
            key, rec = self._file(s, name)
            out = s.client.call("edit", name=key, ops=ops)
            rec.dirty = True
            s.counters["edits"] += 1
            s.audit.write("edit", name=key, ops=[o.get("op") for o in ops if isinstance(o, dict)],
                          sha256=out["info"]["sha256"])
            return out

    def create(self, name: str, content=None) -> dict:
        with self.lock:
            s = self._require()
            key = normalize_name(name)
            if key in s.files:
                raise WorkbenchError(f"'{key}'가 이미 있습니다.", code="exists")
            out = s.client.call("create", name=key, content=content)
            s.files[key] = FileRec(kind=out["info"]["kind"], dirty=True)
            s.counters["edits"] += 1
            s.audit.write("create", name=key, kind=out["info"]["kind"])
            return out

    def undo(self, name: str) -> dict:
        with self.lock:
            s = self._require()
            key, rec = self._file(s, name)
            out = s.client.call("revert", name=key)
            rec.dirty = True
            s.audit.write("undo", name=key, sha256=out["info"]["sha256"])
            return out

    def delete(self, name: str, discard: bool = False) -> dict:
        """작업구역 안의 사본만 지운다. 호스트의 원본 파일은 건드리지 않는다."""
        with self.lock:
            s = self._require()
            key, rec = self._file(s, name)
            if rec.dirty and not discard:
                raise WorkbenchError(
                    f"'{key}'에 내보내지 않은 수정이 있습니다. workbench_export로 저장하거나, 버려도 되면 "
                    "discard=true로 지우세요. 호스트의 원본은 어느 쪽이든 그대로입니다.", code="unexported_changes")
            s.client.call("delete", name=key)
            del s.files[key]
            s.audit.write("delete", name=key)
            return {"deleted": key}

    # --- 내보내기: 재보호는 여기서만 일어난다 ---------------------------------
    def export(self, name: str, dest_path: str, overwrite: bool = False, force: bool = False) -> dict:
        with self.lock:
            s = self._require()
            key, rec = self._file(s, name)
            dest = Path(dest_path).expanduser()
            overwrites_source = bool(rec.source) and _same_path(rec.source, dest)
            if overwrites_source and overwrite and not force and dest.exists() and rec.source_sig is not None \
                    and _signature(dest) != rec.source_sig:
                raise WorkbenchError(
                    f"원본 '{dest}'이(가) 작업구역에서 열린 뒤에 다른 곳에서 바뀌었습니다. 그대로 덮어쓰면 그 변경이 "
                    "사라집니다. 다른 이름으로 내보내 비교하거나, 변경을 버려도 되는 것을 확인했다면 force=true로 "
                    "다시 내보내세요.", code="source_changed")
            if not is_within(dest, self.settings.export_roots):
                raise WorkbenchError("허용된 폴더 밖으로는 내보낼 수 없습니다. 내보낼 수 있는 폴더: "
                                     + ", ".join(str(r) for r in self.settings.export_roots), code="denied")
            got = s.client.call("get", name=key)
            data = b64d(got["data_b64"])
            try:
                final = self.nasca.protect(data, str(dest), overwrite=overwrite)
            finally:
                del data
            s.counters["protects"] += 1
            rec.dirty = False
            rec.exported_to = final
            if overwrites_source:
                rec.source_sig = _signature(dest)  # 우리가 쓴 결과를 새 기준으로 삼는다. 이어서 다시 내보내도 충돌이 아니다
            s.audit.write("export", name=key, dest=final, sha256=got["sha256"], size=got["size"])
            return {"exported": key, "path": final, "size": got["size"], "protected_by": self.nasca.name}

    def close(self, discard: bool = False) -> dict:
        with self.lock:
            s = self._require()
            dirty = sorted(n for n, f in s.files.items() if f.dirty)
            if dirty and not discard:
                raise WorkbenchError(
                    f"내보내지 않은 수정이 있는 파일이 있습니다: {', '.join(dirty)}. workbench_export로 저장하거나, "
                    "버려도 되면 discard=true로 닫으세요.", code="unexported_changes")
            return self._end(s, "사용자 종료", discard=discard)
