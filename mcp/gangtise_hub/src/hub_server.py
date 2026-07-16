"""Gangtise MCP Hub (stdio) — 方案 A：仅暴露 5 个域入口，list/read_ref/call 渐进披露。

HTTP/SSE 请用 api/gangtise_hub（gangtise-hub-api）。
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from contextlib import redirect_stdout
from contextvars import copy_context
from pathlib import Path
from typing import Any, Dict, List


def _ensure_layer_paths() -> None:
    here = Path(__file__).resolve().parent
    paths = [here]
    if here.name == "src":
        mcp_root = here.parents[1]
        for dom in (
            "gangtise_agent",
            "gangtise_data",
            "gangtise_file",
            "gangtise_kb",
            "gangtise_private",
        ):
            paths.append(mcp_root / dom / "src")
    for p in paths:
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)


_ensure_layer_paths()

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import EmbeddedResource, TextContent, Tool

from authorization import is_auth_configured
from domains import DOMAINS, ROUTER_INPUT_SCHEMA, domain_tool_description
from package_loader import preload_all
from result_attachments import with_path_attachments
from router import route
from tool_errors import tool_error

SERVER_NAME = "gangtise-hub-mcp"
SERVER_VERSION = "0.1.0"


def _auth_missing_message() -> str:
    return (
        "未配置 Authorization。\n"
        "HTTP：请在请求头携带 Authorization: Bearer <token>\n"
        "stdio：设置环境变量 GTS_AUTHORIZATION 或本地 authorization 文件"
    )


def _check_auth_env() -> str | None:
    if is_auth_configured():
        return None
    return _auth_missing_message()


def _normalize_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    return str(result)


server = Server(SERVER_NAME)


@server.list_tools()
async def list_tools() -> List[Tool]:
    return [
        Tool(
            name=d.tool_name,
            description=domain_tool_description(d),
            inputSchema=ROUTER_INPUT_SCHEMA,
        )
        for d in DOMAINS
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: Dict[str, Any]
) -> Any:
    args = dict(arguments or {})
    action = str(args.get("action") or "list").strip().lower()

    if action == "call":
        auth_err = _check_auth_env()
        if auth_err:
            return tool_error(auth_err, code="UNAUTHORIZED")

    text, invoke, _runtime = route(name, args)
    if invoke is None:
        if text and (text.startswith("未知") or "必须提供" in text or "不是合法" in text):
            return tool_error(text, code="INVALID_PARAMS")
        return [TextContent(type="text", text=text or "(empty)")]

    handler, filtered = invoke
    try:
        ctx = copy_context()

        def _invoke():
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = handler(**filtered)
            return out, buf.getvalue()

        result, stdout_text = await asyncio.to_thread(ctx.run, _invoke)
    except TypeError as e:
        return tool_error(f"参数错误: {e}", code="INVALID_PARAMS")
    except Exception as e:
        return tool_error(f"调用失败: {e}", code="INTERNAL_ERROR")

    out_text = _normalize_result(result)
    if stdout_text:
        out_text = stdout_text + out_text
    attach = os.getenv("MCP_ATTACH_FILES", "").lower() in ("1", "true", "yes", "on")
    return with_path_attachments(out_text, enabled=attach)



async def _run_stdio() -> None:
    preload_all()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main(argv: list[str] | None = None) -> None:
    _ = argv
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
