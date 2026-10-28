"""에이전트의 RPC 메서드. 모든 메서드는 JSON으로 직렬화되는 dict를 돌려준다."""

from __future__ import annotations

import base64
import os
import posixpath
from pathlib import Path

from ..errors import WorkbenchError
from . import docs_docx, docs_image, docs_pptx, docs_text, docs_xlsx
from .kinds import kind_of
from .ooxml import list_media, read_media
from .store import Store, sha256_bytes

KIND_MODULES = {"docx": docs_docx, "xlsx": docs_xlsx, "pptx": docs_pptx, "image": docs_image, "text": docs_text}
OOXML_KINDS = ("docx", "xlsx", "pptx")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        raise WorkbenchError("data_b64가 올바른 base64가 아닙니다.", code="bad_op") from None


class Dispatcher:
    def __init__(self, store: Store):
        self.store = store

    def call(self, method: str, params: dict) -> dict:
        fn = getattr(self, f"m_{method}", None)
        if fn is None:
            raise WorkbenchError(f"알 수 없는 메서드: {method}", code="bad_method")
        return fn(**(params or {}))

    # --- 공통 ---------------------------------------------------------
    def _module(self, name: str):
        kind = kind_of(name)
        mod = KIND_MODULES.get(kind)
        if mod is None:
            raise WorkbenchError(
                f"'{name}'은(는) 지원하지 않는 형식입니다. 읽기·편집은 docx, xlsx, pptx, 이미지, 텍스트만 됩니다. "
                "내보내기만 가능합니다.", code="unsupported")
        return kind, mod

    def m_ping(self) -> dict:
        return {"files": len(self.store.list()), "bytes": self.store.total_bytes()}

    def m_list(self) -> dict:
        return {"files": self.store.list()}

    def m_put(self, name: str, data_b64: str, overwrite: bool = False) -> dict:
        info = self.store.put(name, _unb64(data_b64), overwrite=overwrite)
        out = {"info": info}
        if info["kind"] in KIND_MODULES:
            try:
                out["outline"] = KIND_MODULES[info["kind"]].outline(self.store.path(name))
            except WorkbenchError as e:
                out["outline_error"] = str(e)
            except Exception as e:
                out["outline_error"] = (
                    f"{info['kind']} 파일로 해석하지 못했습니다({type(e).__name__}). 복호화가 덜 됐거나 손상된 파일일 수 있습니다."
                )
        return out

    def m_outline(self, name: str) -> dict:
        self.store.require(name)
        _, mod = self._module(name)
        return mod.outline(self.store.path(name))

    def m_read(self, name: str, start: int | None = None, end: int | None = None, sheet: str | None = None,
               cell_range: str | None = None, mode: str = "formulas", max_chars: int = 20000) -> dict:
        path = self.store.require(name)
        kind, mod = self._module(name)
        max_chars = max(500, min(int(max_chars), 100_000))
        if kind == "image":
            raise WorkbenchError("이미지는 workbench_view_image로 봅니다.", code="bad_op")
        if kind == "xlsx":
            return mod.read(path, sheet=sheet, cell_range=cell_range, mode=mode, max_chars=max_chars)
        default_start = 1 if kind in ("pptx", "text") else 0
        return mod.read(path, start=default_start if start is None else int(start),
                        end=None if end is None else int(end), max_chars=max_chars)

    def m_edit(self, name: str, ops: list) -> dict:
        path = self.store.require(name)
        kind, mod = self._module(name)
        tmp, result = mod.edit(path, ops)
        try:
            info = self.store.replace_with_file(name, tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        result["info"] = info
        return result

    def m_create(self, name: str, content=None) -> dict:
        kind = kind_of(name)
        if kind not in ("docx", "xlsx", "pptx", "text"):
            raise WorkbenchError("새로 만들 수 있는 형식은 docx, xlsx, pptx, 텍스트입니다.", code="unsupported")
        path = self.store.path(name)
        if path.exists():
            raise WorkbenchError(f"'{self.store.rel(path)}'가 이미 있습니다. 다른 이름을 쓰세요.", code="exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "text":
            path.write_text(str(content or ""), encoding="utf-8")
        else:
            KIND_MODULES[kind].create(path, content)
        out = {"info": self.store.info(name)}
        out["outline"] = KIND_MODULES[kind].outline(path)
        return out

    def m_image(self, name: str, max_side: int = 1568, crop_px: list | None = None,
                crop_frac: list | None = None) -> dict:
        path = self.store.require(name)
        kind, mod = self._module(name)
        if kind != "image":
            raise WorkbenchError("이미지 파일이 아닙니다. 문서 안의 그림은 workbench_extract_media로 먼저 꺼내세요.",
                                 code="bad_op")
        r = mod.render(path, max_side=int(max_side), crop_px=crop_px, crop_frac=crop_frac)
        data = r.pop("data")
        r["data_b64"] = _b64(data)
        return r

    def m_media(self, name: str) -> dict:
        path = self.store.require(name)
        kind = kind_of(name)
        if kind not in OOXML_KINDS:
            raise WorkbenchError("docx, xlsx, pptx에만 쓸 수 있습니다.", code="bad_op")
        return {"media": [posixpath.basename(m) for m in list_media(path, kind)]}

    def m_extract_media(self, name: str, media: list | None = None) -> dict:
        path = self.store.require(name)
        kind = kind_of(name)
        if kind not in OOXML_KINDS:
            raise WorkbenchError("docx, xlsx, pptx에만 쓸 수 있습니다.", code="bad_op")
        available = [posixpath.basename(m) for m in list_media(path, kind)]
        wanted = media or available
        saved = []
        for m in wanted:
            if m not in available:
                raise WorkbenchError(f"'{m}'은(는) 이 문서의 그림 목록에 없습니다: {', '.join(available)}",
                                     code="not_found")
            dest = f"{self.store.rel(path)}.media/{m}"
            self.store.put(dest, read_media(path, kind, m), overwrite=True)
            saved.append(dest)
        return {"saved": saved}

    def m_get(self, name: str) -> dict:
        data = self.store.read_bytes(name)
        return {"data_b64": _b64(data), "sha256": sha256_bytes(data), "size": len(data)}

    def m_delete(self, name: str) -> dict:
        self.store.delete(name)
        return {"deleted": name}

    def m_revert(self, name: str) -> dict:
        return {"info": self.store.revert(name)}

    def m_wipe(self) -> dict:
        return {"wiped": self.store.wipe()}
