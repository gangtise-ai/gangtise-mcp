"""MCP 工具名到可调用实现的注册表。"""
from __future__ import annotations

from typing import Any, Callable, Dict

from .pdf_parse import pdf_parse

ToolHandler = Callable[..., Any]

TOOL_HANDLERS: Dict[str, ToolHandler] = {
    "pdf_parse": pdf_parse,
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
