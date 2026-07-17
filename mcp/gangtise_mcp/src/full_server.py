"""Gangtise MCP 整合入口（stdio）：一次性暴露五个业务域的全部叶子工具。

HTTP/SSE / gateway 请用 api/gangtise_mcp（gangtise-mcp-api）。
"""
from __future__ import annotations

import asyncio
import inspect
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
from result_attachments import with_path_attachments
from tool_catalog import DOMAIN_PACKAGES, INTERNAL_PARAMS, load_catalog
from tool_errors import tool_error
from url_whitelist import get_white_list, is_tool_allowed, tool_denied_reason

SERVER_NAME = "gangtise-mcp"
SERVER_VERSION = "0.1.0"

server = Server(SERVER_NAME)


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


def _filter_arguments(handler: Any, arguments: Dict[str, Any]) -> tuple[Dict[str, Any], str | None]:
    sig = inspect.signature(handler)
    allowed = {
        name
        for name, p in sig.parameters.items()
        if name not in INTERNAL_PARAMS and p.kind != p.VAR_KEYWORD
    }
    extra = set(arguments or {}) - allowed
    if extra:
        valid = ", ".join(sorted(allowed))
        bad = ", ".join(sorted(extra))
        return {}, f"未知参数: {bad}。有效参数: {valid}"
    filtered = {k: v for k, v in (arguments or {}).items() if k in allowed}
    return filtered, None


def _normalize_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    return str(result)


@server.list_tools()
async def list_tools() -> List[Tool]:
    cat = load_catalog()
    wl = get_white_list()
    tools: List[Tool] = []
    for spec in cat.specs:
        if spec.name not in cat.handlers:
            continue
        if not is_tool_allowed(spec.name, wl):
            continue
        tools.append(
            Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_schema,
            )
        )
    return tools


@server.call_tool()
async def call_tool(
    name: str, arguments: Dict[str, Any]
) -> Any:
    auth_err = _check_auth_env()
    if auth_err:
        return tool_error(auth_err, code="UNAUTHORIZED")

    denied = tool_denied_reason(name)
    if denied:
        return tool_error(f"无权限调用工具 {name}: {denied}", code="FORBIDDEN")

    cat = load_catalog()
    handler = cat.handlers.get(name)
    if handler is None:
        return tool_error(f"未知工具: {name}", code="UNKNOWN_TOOL")

    filtered, param_err = _filter_arguments(handler, arguments or {})
    if param_err:
        return tool_error(param_err, code="INVALID_PARAMS")
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

    text = _normalize_result(result)
    if stdout_text:
        text = stdout_text + text
    attach = os.getenv("MCP_ATTACH_FILES", "").lower() in ("1", "true", "yes", "on")
    return with_path_attachments(text, enabled=attach)



async def _run_stdio() -> None:
    cat = load_catalog()
    if cat.errors:
        for pkg, err in cat.errors.items():
            print(f"[gangtise-mcp] 警告：未加载 {pkg}: {err}", file=sys.stderr)
    print(
        f"[gangtise-mcp] 已挂载 {len(cat.handlers)} 个叶子工具"
        f"（来自 {', '.join(DOMAIN_PACKAGES)}）",
        file=sys.stderr,
    )
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
