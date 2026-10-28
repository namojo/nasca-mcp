"""MCP 서버를 실제 stdio로 띄워 AI가 보는 그대로 시험한다."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from workbench._mcpcompat import content_mime, result_is_error
from workbench.nasca.mock import is_protected

SRC = str(Path(__file__).resolve().parents[1] / "src")


def server_params(tmp_path, **extra):
    env = {**os.environ, "PYTHONPATH": SRC, "WORKBENCH_BACKEND": "local", "WORKBENCH_NASCA": "mock",
           "WORKBENCH_HOME": str(tmp_path / "home"), "WORKBENCH_IDLE_TTL_S": "300", **extra}
    return StdioServerParameters(command=sys.executable, args=["-m", "workbench.mcp_server"], env=env)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


def text_of(res) -> str:
    return "\n".join(b.text for b in res.content if getattr(b, "type", "") == "text")


def test_full_session_as_the_model_sees_it(tmp_path, protected_docx, protected_png):
    out_path = tmp_path / "out" / "설계서_v2.docx"

    async def go():
        async with stdio_client(server_params(tmp_path)) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                instr = getattr(init, "instructions", None)
                assert instr and "workbench_open" in instr and "workbench_export" in instr

                tools = {t.name: t for t in (await s.list_tools()).tools}
                assert {"workbench_open", "workbench_read", "workbench_edit", "workbench_view_image",
                        "workbench_export", "workbench_close", "workbench_status"} <= set(tools)
                assert "replace_text" in tools["workbench_edit"].description, "op 형식이 설명에 있어야 모델이 쓴다"

                # 세션 전: 상태와 오류가 다음 행동을 알려 준다
                st = text_of(await s.call_tool("workbench_status", {}))
                assert "열려 있는 작업구역이 없다" in st
                res = await s.call_tool("workbench_read", {"name": "x.docx"})
                assert result_is_error(res) and "workbench_open" in text_of(res)

                # 열기
                res = await s.call_tool("workbench_open", {"source_path": str(protected_docx)})
                assert not result_is_error(res), text_of(res)
                opened = json.loads(text_of(res))
                assert opened["was_drm_protected"] and opened["outline"]["headings"][0]["text"] == "1. 개요"
                assert "[작업구역] 파일 1개" in opened["작업구역"]

                # 읽기: 본문과 번호, 끝의 상태 줄
                body = text_of(await s.call_tool("workbench_read", {"name": "설계서.docx"}))
                assert "REST" in body and "[작업구역]" in body

                # 수정과 오류 문구
                res = await s.call_tool("workbench_edit", {"name": "설계서.docx", "ops": [
                    {"op": "replace_text", "find": "REST", "replace": "gRPC"}]})
                assert not result_is_error(res), text_of(res)
                res = await s.call_tool("workbench_edit", {"name": "설계서.docx", "ops": [
                    {"op": "replace_text", "find": "없는문구", "replace": "x"}]})
                assert result_is_error(res) and "아무것도 바뀌지 않았습니다" in text_of(res)

                # 이미지는 이미지 콘텐츠로 온다
                await s.call_tool("workbench_open", {"source_path": str(protected_png)})
                res = await s.call_tool("workbench_view_image", {"name": "구성도.png", "max_side": 400})
                imgs = [b for b in res.content if getattr(b, "type", "") == "image"]
                assert len(imgs) == 1 and content_mime(imgs[0]) in ("image/jpeg", "image/png")
                assert "400x267" in text_of(res)

                # 미반출 수정이 있으면 닫기를 거부한다
                res = await s.call_tool("workbench_close", {})
                assert result_is_error(res) and "설계서.docx" in text_of(res)

                # 내보내기 → 다시 보호됨
                res = await s.call_tool("workbench_export", {"name": "설계서.docx", "dest_path": str(out_path)})
                assert not result_is_error(res), text_of(res)
                assert is_protected(out_path.read_bytes())

                # 상태: 복호화 2회(문서+이미지), 재보호 1회
                st = text_of(await s.call_tool("workbench_status", {}))
                assert "복호화 2회, 재보호 1회" in st

                res = await s.call_tool("workbench_close", {"discard": True})
                assert not result_is_error(res)
                res = await s.call_tool("workbench_read", {"name": "설계서.docx"})
                assert result_is_error(res) and "삭제됐습니다" in text_of(res)

    run(go())


def test_server_exit_wipes_plaintext_even_without_close(tmp_path, protected_docx):
    """클라이언트가 그냥 끊어도 평문이 남지 않는다."""
    roots = []

    async def go():
        async with stdio_client(server_params(tmp_path)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await s.call_tool("workbench_open", {"source_path": str(protected_docx)})
                import tempfile
                roots.extend(p for p in Path(tempfile.gettempdir()).glob("wb-*") if any(p.rglob("*.docx")))
                assert roots, "작업 중에는 평문 사본이 있다"

    run(go())
    import time
    deadline = time.time() + 20
    while time.time() < deadline and any(p.exists() for p in roots):
        time.sleep(0.3)
    assert not any(p.exists() for p in roots), "서버가 끝나면 작업 루트가 지워져야 한다"


def test_policy_roots_apply_through_the_tool_surface(tmp_path, protected_docx):
    async def go():
        params = server_params(tmp_path, WORKBENCH_OPEN_ROOTS=str(tmp_path / "허용"))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("workbench_open", {"source_path": str(protected_docx)})
                assert result_is_error(res) and "허용된 폴더 밖" in text_of(res)

    run(go())


def test_tool_names_constant_matches_registered_tools(tmp_path):
    from workbench.mcp_server import TOOL_NAMES

    async def go():
        async with stdio_client(server_params(tmp_path)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return {t.name for t in (await s.list_tools()).tools}

    assert run(go()) == TOOL_NAMES
