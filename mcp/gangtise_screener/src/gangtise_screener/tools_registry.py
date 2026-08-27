"""MCP 工具名到可调用实现的注册表。"""
from __future__ import annotations

from typing import Any, Callable, Dict

from gangtise_screener import screener

ToolHandler = Callable[..., Any]

TOOL_HANDLERS: Dict[str, ToolHandler] = {
    "screener": screener,
}

INTERNAL_PARAMS = frozenset(
    {
        "headers",
        "authorization",
        "append_file_hint",
        "meta",
        "meta_by_id",
        "indicator_meta",
        "kwargs",
    }
)
