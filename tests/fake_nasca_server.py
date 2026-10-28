"""사내 NASCA MCP를 흉내 내는 시험용 서버. 실제 계약은 사내 서버에 맞춰 어댑터 설정을 고쳐 쓴다."""
import base64
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench._mcpcompat import MCPServer, ToolError  # noqa: E402
from workbench.nasca.mock import is_protected, protect_bytes, unprotect_bytes  # noqa: E402

app = MCPServer("fake-nasca")


@app.tool()
def nasca_decrypt(path: str) -> dict:
    """복호화해 base64로 돌려준다."""
    p = Path(path)
    if not p.is_file():
        raise ToolError(f"파일 없음: {p.name}")
    raw = p.read_bytes()
    data = unprotect_bytes(raw) if is_protected(raw) else raw
    return {"content_base64": base64.b64encode(data).decode(), "grade": "대외비"}


@app.tool()
def nasca_decrypt_to_file(path: str) -> dict:
    """복호화한 파일을 임시 경로에 두고 경로를 돌려준다."""
    raw = Path(path).read_bytes()
    data = unprotect_bytes(raw) if is_protected(raw) else raw
    fd, tmp = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    Path(tmp).write_bytes(data)
    return {"decrypted_path": tmp}


@app.tool()
def nasca_protect(path: str, content_base64: str, overwrite: bool = False) -> dict:
    p = Path(path)
    if p.exists() and not overwrite:
        raise ToolError("이미 있음")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(protect_bytes(base64.b64decode(content_base64)))
    return {"path": str(p)}


@app.tool()
def nasca_protect_file(path: str, src: str) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(protect_bytes(Path(src).read_bytes()))
    return {"path": str(p), "src_seen": Path(src).exists()}


if __name__ == "__main__":
    app.run("stdio")
