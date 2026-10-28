"""격리 환경 안의 파일 저장소.

모든 평문 파일은 root/files 아래에만 놓인다. 수정 전 스냅샷은 root/.rev 아래에 둔다.
wipe()는 파일을 0으로 덮어쓴 뒤 지운다. SSD에서는 덮어쓰기가 완전한 삭제를 보장하지 않으므로,
진짜 소거는 Windows Sandbox 종료(가상 디스크 폐기)가 맡는다.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path

from ..errors import WorkbenchError
from .kinds import kind_of, normalize_name


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, root: str | os.PathLike, *, max_file_bytes: int = 200 * 1024 * 1024,
                 max_revisions: int = 5, max_total_bytes: int = 1024 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.files = self.root / "files"
        self.revs = self.root / ".rev"
        self.files.mkdir(parents=True, exist_ok=True)
        self.revs.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes
        self.max_revisions = max_revisions
        self.max_total_bytes = max_total_bytes
        self.lock = threading.RLock()

    # --- 경로 ---------------------------------------------------------
    def path(self, name: str) -> Path:
        rel = normalize_name(name)
        p = (self.files / rel).resolve()
        if self.files != p and self.files not in p.parents:
            raise WorkbenchError("작업구역 밖의 경로입니다.", code="bad_name")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.files).as_posix()

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def require(self, name: str) -> Path:
        p = self.path(name)
        if not p.is_file():
            have = ", ".join(sorted(f["name"] for f in self.list())[:20]) or "(없음)"
            raise WorkbenchError(
                f"작업구역에 '{normalize_name(name)}' 파일이 없습니다. 현재 파일: {have}", code="not_found"
            )
        return p

    # --- 조회 ---------------------------------------------------------
    def info(self, name: str) -> dict:
        p = self.require(name)
        data = p.read_bytes()
        return {
            "name": self.rel(p),
            "kind": kind_of(p.name),
            "size": len(data),
            "sha256": sha256_bytes(data),
        }

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.files.rglob("*")):
            if p.is_file() and not self._hidden(p):
                st = p.stat()
                out.append({"name": self.rel(p), "kind": kind_of(p.name), "size": st.st_size,
                            "mtime": int(st.st_mtime)})
        return out

    def _hidden(self, p: Path) -> bool:
        rel = p.relative_to(self.files)
        return p.name.endswith(".part") or any(part.startswith(".") for part in rel.parts)

    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.files.rglob("*") if p.is_file())

    def read_bytes(self, name: str) -> bytes:
        return self.require(name).read_bytes()

    # --- 쓰기 ---------------------------------------------------------
    def _check_size(self, size: int, replacing: int = 0) -> None:
        if size > self.max_file_bytes:
            raise WorkbenchError(
                f"파일이 너무 큽니다({size} 바이트). 한도는 {self.max_file_bytes} 바이트입니다.",
                code="too_large",
            )
        if self.total_bytes() - replacing + size > self.max_total_bytes:
            raise WorkbenchError("작업구역 총 용량 한도를 넘습니다.", code="too_large")

    def put(self, name: str, data: bytes, *, overwrite: bool = False) -> dict:
        with self.lock:
            p = self.path(name)
            if p.exists() and not overwrite:
                raise WorkbenchError(
                    f"'{self.rel(p)}'가 이미 있습니다. 다른 이름을 쓰거나 overwrite를 켜세요.", code="exists"
                )
            self._check_size(len(data), p.stat().st_size if p.exists() else 0)
            if p.exists():
                self.snapshot(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            return self.info(name)

    def replace_with_file(self, name: str, tmp_path: Path) -> dict:
        """편집 결과 임시 파일로 원본을 원자적으로 교체한다. 교체 전 스냅샷을 남긴다."""
        with self.lock:
            p = self.require(name)
            self._check_size(tmp_path.stat().st_size, p.stat().st_size)
            self.snapshot(name)
            os.replace(tmp_path, p)
            return self.info(name)

    # --- 스냅샷 -------------------------------------------------------
    def _rev_dir(self, name: str) -> Path:
        return self.revs / normalize_name(name).replace("/", "__")

    def snapshot(self, name: str) -> None:
        p = self.path(name)
        if not p.is_file():
            return
        d = self._rev_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        existing = sorted(d.glob("*.rev"), key=lambda x: int(x.stem))
        n = int(existing[-1].stem) + 1 if existing else 1
        shutil.copyfile(p, d / f"{n}.rev")
        for old in existing[: max(0, len(existing) + 1 - self.max_revisions)]:
            self._shred(old)

    def revert(self, name: str) -> dict:
        with self.lock:
            p = self.require(name)
            d = self._rev_dir(name)
            revs = sorted(d.glob("*.rev"), key=lambda x: int(x.stem)) if d.exists() else []
            if not revs:
                raise WorkbenchError(f"'{self.rel(p)}'에는 되돌릴 이전 버전이 없습니다.", code="no_revision")
            last = revs[-1]
            os.replace(last, p)
            return {**self.info(name), "remaining_revisions": len(revs) - 1}

    def delete(self, name: str) -> None:
        with self.lock:
            p = self.require(name)
            self._shred(p)
            d = self._rev_dir(name)
            if d.exists():
                for r in d.glob("*.rev"):
                    self._shred(r)
                shutil.rmtree(d, ignore_errors=True)
            self._prune_empty_dirs()

    # --- 소거 ---------------------------------------------------------
    @staticmethod
    def _shred(p: Path) -> None:
        try:
            size = p.stat().st_size
            with open(p, "r+b", buffering=0) as f:
                remaining = size
                chunk = b"\x00" * (1024 * 1024)
                while remaining > 0:
                    n = min(remaining, len(chunk))
                    f.write(chunk[:n])
                    remaining -= n
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        try:
            p.unlink()
        except OSError:
            pass

    def _prune_empty_dirs(self) -> None:
        for d in sorted((x for x in self.files.rglob("*") if x.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass

    def wipe(self) -> int:
        with self.lock:
            count = 0
            for base in (self.files, self.revs):
                for p in sorted(base.rglob("*")):
                    if p.is_file():
                        self._shred(p)
                        count += 1
                shutil.rmtree(base, ignore_errors=True)
            self.files.mkdir(parents=True, exist_ok=True)
            self.revs.mkdir(parents=True, exist_ok=True)
            return count
