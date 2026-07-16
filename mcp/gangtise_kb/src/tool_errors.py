"""MCP callTool 错误语义（百炼：isError + JSON text）。"""
from __future__ import annotations

import json
from typing import Any, List, Union

from mcp.types import TextContent

try:
    from mcp.types import CallToolResult
except ImportError:  # pragma: no cover
    CallToolResult = None  # type: ignore[misc, assignment]

ToolResult = Union[List[Any], Any]


def tool_error_text(message: str, code: str = "TOOL_ERROR") -> str:
    return json.dumps({"code": code, "message": message}, ensure_ascii=False)


def tool_error(
    message: str,
    code: str = "TOOL_ERROR",
) -> Any:
    """返回带 isError=True 的 CallToolResult（若 SDK 支持），否则退回 TextContent 列表。"""
    content = [TextContent(type="text", text=tool_error_text(message, code))]
    if CallToolResult is not None:
        return CallToolResult(content=content, isError=True)
    return content


def coerce_json_object_arg(value: Any) -> Any:
    """扁平化后 object 参数以 JSON 字符串传入时，还原为 dict。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"无效 JSON 对象字符串: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError("JSON 字符串须解析为 object")
        return parsed
    raise ValueError(f"期望 object 或 JSON 字符串，收到 {type(value).__name__}")
