"""파일 이름과 종류 판별."""

from __future__ import annotations

from pathlib import PurePosixPath

from ..errors import WorkbenchError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".log", ".ini", ".cfg", ".sql", ".py", ".java", ".js", ".ts", ".sh", ".bat",
}
OOXML = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}

_WIN_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_NAME_LEN = 200
MAX_DEPTH = 4


def kind_of(name: str) -> str:
    ext = PurePosixPath(name).suffix.lower()
    if ext in OOXML:
        return OOXML[ext]
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    return "binary"


def normalize_name(name: str) -> str:
    """작업 루트 기준의 안전한 상대 이름으로 정규화한다. 안전하지 않으면 거부한다."""
    if not isinstance(name, str) or not name.strip():
        raise WorkbenchError("파일 이름이 비어 있습니다.", code="bad_name")
    raw = name.replace("\\", "/").strip()
    if any(ord(c) < 32 for c in raw):
        raise WorkbenchError("파일 이름에 제어 문자가 들어 있습니다.", code="bad_name")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise WorkbenchError(
            "절대 경로는 쓸 수 없습니다. 작업구역 안의 상대 이름만 허용됩니다.", code="bad_name"
        )
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise WorkbenchError("파일 이름이 비어 있습니다.", code="bad_name")
    if len(parts) > MAX_DEPTH:
        raise WorkbenchError(f"하위 폴더는 {MAX_DEPTH - 1}단계까지만 허용됩니다.", code="bad_name")
    for p in parts:
        if p == "..":
            raise WorkbenchError("상위 폴더(..)는 쓸 수 없습니다.", code="bad_name")
        if p.startswith("."):
            raise WorkbenchError("점으로 시작하는 이름은 예약되어 있습니다.", code="bad_name")
        if ":" in p or any(c in p for c in '<>"|?*'):
            raise WorkbenchError(f"이름에 쓸 수 없는 문자가 있습니다: {p}", code="bad_name")
        if p.endswith((" ", ".")):
            raise WorkbenchError(f"이름이 공백이나 점으로 끝날 수 없습니다: {p}", code="bad_name")
        if p.split(".")[0].lower() in _WIN_RESERVED:
            raise WorkbenchError(f"운영체제 예약 이름입니다: {p}", code="bad_name")
    out = "/".join(parts)
    if len(out) > MAX_NAME_LEN:
        raise WorkbenchError("파일 이름이 너무 깁니다.", code="bad_name")
    return out
