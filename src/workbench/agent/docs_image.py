"""이미지 읽기·편집. 모델이 볼 수 있는 크기로 줄여 내보내는 것이 핵심이다."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from ..errors import WorkbenchError

Image.MAX_IMAGE_PIXELS = 120_000_000
DEFAULT_MAX_SIDE = 1568  # 긴 변이 이보다 길면 모델 쪽에서 어차피 줄여 읽는다.


def _open(path: Path) -> Image.Image:
    try:
        im = Image.open(str(path))
        im.load()
    except Exception as e:  # PIL이 다양한 예외를 낸다
        raise WorkbenchError(f"이미지를 열 수 없습니다: {type(e).__name__}", code="bad_file") from None
    fmt = im.format
    im = ImageOps.exif_transpose(im)  # 회전 정보를 반영하면 format이 사라지므로 되살린다
    im.format = fmt
    return im


def outline(path: Path) -> dict:
    im = _open(path)
    fmt = (im.format or Path(path).suffix.lstrip(".")).upper()
    return {"kind": "image", "format": "JPEG" if fmt == "JPG" else fmt,
            "width": im.width, "height": im.height, "mode": im.mode,
            "frames": getattr(im, "n_frames", 1)}


def _crop_box(im: Image.Image, crop_px, crop_frac):
    if crop_px and crop_frac:
        raise WorkbenchError("crop_px와 crop_frac 중 하나만 지정하세요.", code="bad_op")
    if crop_frac:
        if len(crop_frac) != 4 or not all(0 <= v <= 1 for v in crop_frac) or crop_frac[0] >= crop_frac[2] \
                or crop_frac[1] >= crop_frac[3]:
            raise WorkbenchError("crop_frac는 0~1 사이의 [왼쪽, 위, 오른쪽, 아래]이고 왼쪽<오른쪽, 위<아래여야 합니다.",
                                 code="bad_op")
        w, h = im.size
        return (round(crop_frac[0] * w), round(crop_frac[1] * h), round(crop_frac[2] * w), round(crop_frac[3] * h))
    if crop_px:
        if len(crop_px) != 4:
            raise WorkbenchError("crop_px는 [왼쪽, 위, 오른쪽, 아래] 4개 정수입니다.", code="bad_op")
        l, t, r, b = (int(v) for v in crop_px)
        l, t = max(0, l), max(0, t)
        r, b = min(im.width, r), min(im.height, b)
        if l >= r or t >= b:
            raise WorkbenchError(f"잘라낼 영역이 비었습니다. 이미지 크기는 {im.width}x{im.height}입니다.", code="bad_op")
        return (l, t, r, b)
    return None


def render(path: Path, *, max_side: int = DEFAULT_MAX_SIDE, crop_px=None, crop_frac=None) -> dict:
    """모델에게 보여 줄 이미지를 만든다. 잘라낸 뒤 긴 변을 max_side로 줄인다."""
    if not 64 <= max_side <= 4096:
        raise WorkbenchError("max_side는 64~4096 사이여야 합니다.", code="bad_op")
    im = _open(path)
    orig = im.size
    box = _crop_box(im, crop_px, crop_frac)
    if box:
        im = im.crop(box)
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    has_alpha = im.mode in ("RGBA", "LA", "P") and ("transparency" in im.info or im.mode in ("RGBA", "LA"))
    buf = io.BytesIO()
    if has_alpha or max(im.size) <= 512:
        im.convert("RGBA" if has_alpha else "RGB").save(buf, "PNG", optimize=True)
        mime, fmt = "image/png", "png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
        mime, fmt = "image/jpeg", "jpeg"
    return {"data": buf.getvalue(), "mime": mime, "format": fmt, "width": im.width, "height": im.height,
            "original_width": orig[0], "original_height": orig[1], "crop_box": list(box) if box else None}


def _apply_op(im: Image.Image, op: dict, k: int, details: list) -> Image.Image:
    kind = op.get("op")
    if kind == "crop":
        box = _crop_box(im, op.get("crop_px"), op.get("crop_frac"))
        if box is None:
            raise WorkbenchError(f"ops[{k}] crop에는 crop_px 또는 crop_frac이 필요합니다.", code="bad_op")
        im = im.crop(box)
        details.append({"op": "crop", "box": list(box), "size": list(im.size)})
    elif kind == "rotate":
        deg = float(op.get("degrees", 0))
        im = im.rotate(-deg, expand=True)  # 양수는 시계 방향
        details.append({"op": "rotate", "degrees": deg, "size": list(im.size)})
    elif kind == "resize":
        side = int(op.get("max_side", 0))
        if side < 16:
            raise WorkbenchError(f"ops[{k}] resize에는 max_side(16 이상)가 필요합니다.", code="bad_op")
        im = im.copy()
        im.thumbnail((side, side), Image.LANCZOS)
        details.append({"op": "resize", "size": list(im.size)})
    elif kind == "flip":
        im = ImageOps.mirror(im) if op.get("axis", "horizontal") == "horizontal" else ImageOps.flip(im)
        details.append({"op": "flip", "axis": op.get("axis", "horizontal")})
    else:
        raise WorkbenchError(f"ops[{k}] 알 수 없는 op '{kind}'. 쓸 수 있는 op: crop, rotate, resize, flip",
                             code="bad_op")
    return im


def edit(path: Path, ops: list[dict]) -> tuple[Path, dict]:
    if not ops:
        raise WorkbenchError("ops가 비어 있습니다.", code="bad_op")
    im = _open(path)
    fmt = (im.format or path.suffix.lstrip(".")).upper()
    if fmt == "JPG":
        fmt = "JPEG"
    details: list[dict] = []
    for k, op in enumerate(ops):
        if not isinstance(op, dict):
            raise WorkbenchError(f"ops[{k}]는 객체여야 합니다.", code="bad_op")
        im = _apply_op(im, op, k, details)
    fd, tmp = tempfile.mkstemp(prefix=".edit-", suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    save_im = im.convert("RGB") if fmt == "JPEG" and im.mode not in ("RGB", "L") else im
    save_im.save(tmp, fmt if fmt in ("PNG", "JPEG", "GIF", "BMP", "WEBP", "TIFF") else "PNG")
    return Path(tmp), {"applied": len(ops), "details": details, "width": im.width, "height": im.height,
                       "warnings": ["이미지 편집은 다시 저장되므로 JPEG는 화질이 조금 떨어질 수 있습니다."]
                       if fmt == "JPEG" else []}
