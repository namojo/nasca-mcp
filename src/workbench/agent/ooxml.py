"""OOXML(docx/xlsx/pptx) 공통 보조 함수."""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path

MEDIA_PREFIX = {"docx": "word/media/", "xlsx": "xl/media/", "pptx": "ppt/media/"}
RISKY_PARTS = {
    "xlsx": ("xl/charts/", "xl/drawings/", "xl/pivotTables/", "xl/vbaProject.bin", "xl/media/"),
    "docx": ("word/vbaProject.bin",),
    "pptx": ("ppt/vbaProject.bin",),
}


def list_media(path: Path, kind: str) -> list[str]:
    prefix = MEDIA_PREFIX[kind]
    with zipfile.ZipFile(path) as z:
        return sorted(n for n in z.namelist() if n.startswith(prefix) and not n.endswith("/"))


def read_media(path: Path, kind: str, media_name: str) -> bytes:
    prefix = MEDIA_PREFIX[kind]
    full = media_name if media_name.startswith(prefix) else prefix + media_name
    with zipfile.ZipFile(path) as z:
        return z.read(full)


def lossy_save_warnings(path: Path, kind: str) -> list[str]:
    """라이브러리로 다시 저장할 때 유실될 수 있는 요소가 있는지 알려 준다."""
    risky = RISKY_PARTS.get(kind, ())
    found: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return found
    for part in risky:
        if any(n.startswith(part) for n in names):
            found.append(part.rstrip("/"))
    if not found:
        return []
    return [
        "이 파일에는 " + ", ".join(found) + " 요소가 있습니다. 편집 후 저장하면 차트·그림·매크로 등이 "
        "유실되거나 모양이 바뀔 수 있으니 결과를 반드시 다시 확인하세요."
    ]


def blip_media_names(element, part) -> list[str]:
    """요소 안의 그림(a:blip)이 가리키는 미디어 파일 이름 목록."""
    names: list[str] = []
    ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    for blip in element.iter(f"{{{ns_a}}}blip"):
        rid = blip.get(f"{{{ns_r}}}embed")
        if not rid:
            continue
        try:
            related = part.related_part(rid) if hasattr(part, "related_part") else part.related_parts[rid]
            names.append(posixpath.basename(str(related.partname)))
        except Exception:
            names.append(f"(rId={rid})")
    return names


def clip(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True
