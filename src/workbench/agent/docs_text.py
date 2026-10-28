"""텍스트 파일 읽기·편집. 한국어 환경이라 UTF-8과 CP949를 모두 다룬다."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..errors import WorkbenchError


def detect(data: bytes) -> tuple[str, str]:
    """(인코딩, 디코드된 텍스트)."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return enc, data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise WorkbenchError("텍스트로 읽을 수 없는 파일입니다(UTF-8, CP949가 아님).", code="bad_file")


def _encode(enc: str, text: str) -> bytes:
    return text.encode("utf-8" if enc == "utf-8-sig" and not text.startswith("﻿") else enc)


def outline(path: Path) -> dict:
    enc, text = detect(path.read_bytes())
    return {"kind": "text", "encoding": enc, "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "chars": len(text)}


def read(path: Path, *, start: int = 1, end: int | None = None, max_chars: int = 20000) -> dict:
    enc, text = detect(path.read_bytes())
    lines = text.splitlines()
    total = len(lines)
    end = total if end is None else min(end, total)
    start = max(1, start)
    out: list[str] = []
    used = 0
    next_start = None
    for n in range(start, end + 1):
        line = f"{n:>5}  {lines[n - 1]}"
        if used + len(line) > max_chars and out:
            next_start = n
            break
        out.append(line)
        used += len(line) + 1
    return {"text": "\n".join(out), "total_lines": total, "next_start": next_start, "encoding": enc}


def edit(path: Path, ops: list[dict]) -> tuple[Path, dict]:
    if not ops:
        raise WorkbenchError("ops가 비어 있습니다.", code="bad_op")
    enc, text = detect(path.read_bytes())
    details: list[dict] = []
    for k, op in enumerate(ops):
        kind = op.get("op") if isinstance(op, dict) else None
        if kind == "write":
            text = str(op.get("text", ""))
        elif kind == "append":
            text += str(op.get("text", ""))
        elif kind == "replace_text":
            find = op.get("find")
            if not find:
                raise WorkbenchError(f"ops[{k}] replace_text에는 find가 필요합니다.", code="bad_op")
            n = text.count(find)
            if n == 0:
                raise WorkbenchError(f"ops[{k}] '{find}'를 찾지 못했습니다. 아무것도 바뀌지 않았습니다.", code="not_found")
            text = text.replace(find, str(op.get("replace", "")))
            details.append({"op": "replace_text", "replaced": n})
            continue
        elif kind in ("insert_lines", "delete_lines"):
            lines = text.splitlines(keepends=True)
            if kind == "insert_lines":
                after = int(op.get("after_line", 0))
                if not 0 <= after <= len(lines):
                    raise WorkbenchError(f"ops[{k}] after_line은 0~{len(lines)}입니다.", code="bad_index")
                new = str(op.get("text", ""))
                if new and not new.endswith("\n"):
                    new += "\n"
                if after > 0 and lines and not lines[after - 1].endswith("\n"):
                    lines[after - 1] += "\n"
                lines[after:after] = [new]
            else:
                s, e = int(op.get("start", 0)), int(op.get("end", op.get("start", 0)))
                if not (1 <= s <= e <= len(lines)):
                    raise WorkbenchError(f"ops[{k}] 줄 범위는 1~{len(lines)} 안에서 start<=end여야 합니다.",
                                         code="bad_index")
                del lines[s - 1:e]
            text = "".join(lines)
        else:
            raise WorkbenchError(
                f"ops[{k}] 알 수 없는 op '{kind}'. 쓸 수 있는 op: write, append, replace_text, insert_lines, delete_lines",
                code="bad_op")
        details.append({"op": kind})
    fd, tmp = tempfile.mkstemp(prefix=".edit-", suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    Path(tmp).write_bytes(_encode(enc, text))
    return Path(tmp), {"applied": len(ops), "details": details, "encoding": enc, "warnings": []}
