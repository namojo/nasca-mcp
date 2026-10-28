"""mcp SDK 1.x(FastMCP)와 2.x(MCPServer)를 같은 이름으로 쓰기 위한 얇은 호환 계층."""

from __future__ import annotations

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[assignment]
    from mcp.server.fastmcp import Image  # type: ignore[assignment]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[assignment]

__all__ = ["Image", "MCPServer", "ToolError"]


def result_is_error(result) -> bool:
    """CallToolResult의 오류 여부. 1.x는 isError, 2.x는 is_error."""
    for attr in ("isError", "is_error"):
        if hasattr(result, attr):
            return bool(getattr(result, attr))
    return False


def content_mime(block) -> str | None:
    """ImageContent의 MIME. 1.x는 mimeType, 2.x는 mime_type."""
    for attr in ("mimeType", "mime_type"):
        if hasattr(block, attr):
            return getattr(block, attr)
    return None
