"""작업구역 MCP 서버. AI가 보호 문서에 닿는 유일한 문."""

from __future__ import annotations

import functools
import logging
import signal
import sys
from typing import Any

from ._mcpcompat import Image, MCPServer, ToolError
from .broker.manager import Manager
from .config import Settings
from .errors import WorkbenchError
from .manifest import SERVER_INSTRUCTIONS, footer, render
from .nasca.factory import build_nasca

log = logging.getLogger("workbench")

EDIT_OPS_HELP = """\
ops는 순서대로 적용되며 하나라도 실패하면 아무것도 바뀌지 않는다. 삽입·삭제 뒤에는 번호가 밀리므로 여러 곳을 \
고칠 때는 뒤쪽부터 고치거나 다시 읽어 번호를 확인한다. 파일 형식별 op:
- docx(i는 read가 보여 준 블록 번호): replace_text{find,replace} | set_text{i,text} | insert_after{i,text,style?} | \
insert_before{i,text,style?} | delete{i} | set_style{i,style} | set_cell{i,row,col,text} | add_row{i,cells}
- xlsx: set_cells{sheet?,cells:{"B2":값,"C2":"=B2*2"}} | set_range{sheet?,top_left,values:[[..]]} | \
append_rows{sheet?,rows:[[..]]} | clear_range{sheet?,cell_range} | add_sheet{name} | rename_sheet{sheet,name} | delete_sheet{sheet}
- pptx(slide는 1부터): set_text{slide,shape_id,text} | replace_text{find,replace,slide?} | set_notes{slide,text} | \
add_slide{layout?,title?,body?} | delete_slide{slide}
- 이미지: crop{crop_px:[l,t,r,b]|crop_frac:[l,t,r,b]} | rotate{degrees} | resize{max_side} | flip{axis}
- 텍스트: write{text} | append{text} | replace_text{find,replace} | insert_lines{after_line,text} | delete_lines{start,end}"""


_EDIT_DOC = ("작업구역 안의 문서를 고친다(원본 보호 파일은 그대로). 수정은 내보내기 전까지 이 세션에만 있다.\n"
             + EDIT_OPS_HELP)


def build_server(manager: Manager) -> MCPServer:
    app = MCPServer("workbench", instructions=SERVER_INSTRUCTIONS)

    def tool(fn=None, *, description: str | None = None):
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                try:
                    return fn(*a, **kw)
                except WorkbenchError as e:
                    raise ToolError(f"{e}\n{footer(manager.brief())}") from None
                except ToolError:
                    raise
                except Exception as e:  # noqa: BLE001  라이브러리 예외 문구에 문서 조각이 있을 수 있어 형식만 알린다
                    log.exception("tool failed")
                    raise ToolError(f"내부 오류({type(e).__name__}). 작업구역 로그를 확인하세요.") from None

            return app.tool(description=description)(wrapper) if description else app.tool()(wrapper)

        return deco(fn) if fn is not None else deco

    def with_footer(d: dict) -> dict:
        return {**d, "작업구역": footer(manager.brief())}

    @tool
    def workbench_status() -> str:
        """작업구역의 현재 상태와 규칙을 보여 준다. 세션이 없으면 없다고 알려 준다."""
        return render(manager.status())

    @tool
    def workbench_open(source_path: str, name: str | None = None) -> dict:
        """보호된(또는 일반) 문서를 복호화해 작업구역에 넣는다. 처음 열면 격리된 작업구역 세션이 시작된다.
        한 번 열면 닫을 때까지 복호화된 상태로 남으므로 같은 문서를 다시 열 필요가 없다.
        source_path는 호스트의 파일 경로, name은 작업구역 안에서 쓸 이름(생략하면 파일 이름).
        결과의 outline으로 구조를 보고 필요한 구간만 workbench_read로 읽는다."""
        return with_footer(manager.open(source_path, name))

    @tool
    def workbench_list() -> dict:
        """작업구역 안의 파일 목록."""
        return with_footer(manager.list_files())

    @tool
    def workbench_outline(name: str) -> dict:
        """문서 구조(제목, 표, 시트, 슬라이드, 그림 수 등)만 본다. 본문은 보내지 않아 토큰이 적게 든다."""
        return with_footer(manager.outline(name))

    @tool
    def workbench_read(name: str, start: int | None = None, end: int | None = None, sheet: str | None = None,
                       cell_range: str | None = None, mode: str | None = None, max_chars: int | None = None) -> str:
        """문서의 일부를 읽는다. 결과 끝에 다음 구간 안내가 있으면 이어서 읽는다.
        - docx: start,end는 블록 번호(0부터). 각 줄 앞의 [번호]를 edit에서 쓴다.
        - xlsx: sheet와 cell_range(예: A1:F40). mode는 formulas(수식 보기, 기본) 또는 values(계산된 값).
        - pptx: start,end는 슬라이드 번호(1부터). shape id를 edit에서 쓴다.
        - 텍스트: start,end는 줄 번호(1부터).
        - 이미지는 workbench_view_image로 본다."""
        out = manager.read(name, start=start, end=end, sheet=sheet, cell_range=cell_range, mode=mode,
                           max_chars=max_chars)
        text = out["text"]
        more = out.get("next_start") if out.get("next_start") is not None else out.get("next_row")
        if more is not None:
            text += f"\n… 이어서 읽으려면 start={more}부터(xlsx는 cell_range의 시작 행 {more}부터)."
        return text + "\n\n" + footer(manager.brief())

    @tool
    def workbench_view_image(name: str, max_side: int = 1568, crop_px: list[int] | None = None,
                             crop_frac: list[float] | None = None) -> list:
        """작업구역의 이미지를 직접 본다. 화면 캡처보다 토큰이 적고 글자가 깨지지 않는다.
        세부가 필요하면 crop_frac=[왼쪽,위,오른쪽,아래](각 0~1)로 영역을 잘라 확대해서 다시 본다.
        문서(docx/pptx/xlsx) 안의 그림은 workbench_extract_media로 먼저 꺼낸다."""
        r = manager.view_image(name, max_side=max_side, crop_px=crop_px, crop_frac=crop_frac)
        cap = (f"{name} {r['width']}x{r['height']}(원본 {r['original_width']}x{r['original_height']})"
               + (f", 잘라낸 영역 {r['crop_box']}" if r.get("crop_box") else ""))
        return [Image(data=r["data"], format=r["format"]), cap + "\n" + footer(manager.brief())]

    @tool
    def workbench_extract_media(name: str, media: list[str] | None = None) -> dict:
        """docx/pptx/xlsx 안에 삽입된 그림을 작업구역 파일로 꺼낸다. read 결과의 [그림:이름]이 그 이름이다.
        media를 생략하면 전부 꺼낸다. 꺼낸 그림은 workbench_view_image로 본다."""
        return with_footer(manager.extract_media(name, media))

    @tool(description=_EDIT_DOC)
    def workbench_edit(name: str, ops: list[dict[str, Any]]) -> dict:
        return with_footer(manager.edit(name, ops))

    @tool
    def workbench_create(name: str, content: Any = None) -> dict:
        """새 문서를 작업구역 안에 만든다. docx: content=[문자열 | {heading,level} | {p,style} | {table:[[..]]}], \
xlsx: {sheets:{시트이름:[[..]]}}, pptx: {slides:[{title,body}]}, 텍스트: 문자열."""
        return with_footer(manager.create(name, content))

    @tool
    def workbench_undo(name: str) -> dict:
        """마지막 수정을 되돌린다(파일마다 최근 5개 버전)."""
        return with_footer(manager.undo(name))

    @tool
    def workbench_delete(name: str, discard: bool = False) -> dict:
        """작업구역 안의 사본만 지운다(평문 소거). 호스트의 원본 파일은 건드리지 않는다.
        내보내지 않은 수정이 있으면 거부하며, 버려도 될 때만 discard=true로 지운다."""
        return with_footer(manager.delete(name, discard))

    @tool
    def workbench_export(name: str, dest_path: str, overwrite: bool = False, force: bool = False) -> dict:
        """작업구역의 파일을 DRM으로 다시 보호해 호스트의 dest_path에 저장한다. 평문이 밖으로 나가는 유일한 경로이며
        항상 재보호된다. 같은 이름이 있으면 overwrite=true일 때만 덮어쓴다. 열어 둔 원본을 덮어쓰려는데 그 사이
        원본이 바뀌었으면 거부한다(다른 이름으로 내보내거나, 변경을 버려도 되면 force=true)."""
        return with_footer(manager.export(name, dest_path, overwrite, force))

    @tool
    def workbench_close(discard: bool = False) -> dict:
        """작업을 끝내고 작업구역을 닫는다. 복호화된 파일이 모두 삭제된다. 내보내지 않은 수정이 있으면 거부하며,
        버려도 될 때만 discard=true로 닫는다."""
        return manager.close(discard)

    return app


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    manager = Manager(settings, build_nasca(settings.nasca, str(settings.nasca_mcp_config) if settings.nasca_mcp_config else None))
    if settings.backend == "local":
        log.warning("로컬 백엔드: 격리가 없는 개발용입니다.")

    def _stop(signum, frame):  # noqa: ARG001
        manager.shutdown()
        raise SystemExit(0)

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None), getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass
    build_server(manager).run("stdio")
    manager.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())


TOOL_NAMES = {
    "workbench_status", "workbench_open", "workbench_list", "workbench_outline", "workbench_read",
    "workbench_view_image", "workbench_extract_media", "workbench_edit", "workbench_create", "workbench_undo",
    "workbench_delete", "workbench_export", "workbench_close",
}
