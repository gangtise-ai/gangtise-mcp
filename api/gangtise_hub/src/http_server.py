"""Gangtise Hub MCP HTTP/SSE — OAuth/鉴权在本包；路由逻辑来自 mcp/gangtise_hub。

仅 `--transport http|sse|both`（默认 both）。stdio 请用 gangtise-hub-mcp。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from contextvars import copy_context
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple


def _ensure_layer_paths() -> None:
    here = Path(__file__).resolve().parent
    paths = [here]
    if here.name == "src":
        pkg = here.parent.name
        mcps_root = here.parents[2]
        paths.append(mcps_root / "mcp" / pkg / "src")
        for dom in (
            "gangtise_agent",
            "gangtise_data",
            "gangtise_file",
            "gangtise_kb",
            "gangtise_private",
        ):
            paths.append(mcps_root / "mcp" / dom / "src")
    for p in paths:
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)


_ensure_layer_paths()

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import EmbeddedResource, TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from authorization import (
    is_auth_configured,
    reset_request_authorization,
    reset_request_credentials,
    reset_request_headers_extra,
    set_request_authorization,
    set_request_credentials,
    set_request_headers_extra,
)
from domains import DOMAINS, ROUTER_INPUT_SCHEMA, domain_tool_description
from http_compat import HttpMiddleware
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
        if text and text.startswith("未知"):
            return tool_error(text, code="UNKNOWN_TOOL")
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
    attach = True
    if os.getenv("MCP_ATTACH_FILES", "").lower() in ("0", "false", "no", "off"):
        attach = False
    return with_path_attachments(out_text, enabled=attach)




def _wrap_http_middleware(app: ASGIApp, *, mcp_paths: set[str]) -> ASGIApp:
    return HttpMiddleware(
        app,
        set_authorization=set_request_authorization,
        reset_authorization=reset_request_authorization,
        set_credentials=set_request_credentials,
        reset_credentials=reset_request_credentials,
        set_headers_extra=set_request_headers_extra,
        reset_headers_extra=reset_request_headers_extra,
        mcp_paths=mcp_paths,
    )


class _StreamableHTTPASGIApp:
    def __init__(self, session_manager: StreamableHTTPSessionManager) -> None:
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)


def _normalize_path(path: str, *, trailing_slash: bool = False) -> str:
    p = path if str(path).startswith("/") else f"/{path}"
    if trailing_slash and not p.endswith("/"):
        p = f"{p}/"
    if not trailing_slash and len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _build_network_app(
    *,
    enable_http: bool,
    enable_sse: bool,
    path: str,
    sse_path: str,
    message_path: str,
    json_response: bool,
    stateless: bool,
) -> ASGIApp:
    path = _normalize_path(path)
    sse_path = _normalize_path(sse_path)
    message_path = _normalize_path(message_path, trailing_slash=True)

    routes: list[Any] = []
    session_manager: StreamableHTTPSessionManager | None = None

    if enable_http:
        session_manager = StreamableHTTPSessionManager(
            app=server,
            json_response=json_response,
            stateless=stateless,
        )
        routes.append(Route(path, endpoint=_StreamableHTTPASGIApp(session_manager)))

    if enable_sse:
        sse = SseServerTransport(message_path)

        async def handle_sse(request: Request) -> Response:
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:  # type: ignore[attr-defined]
                await server.run(
                    streams[0],
                    streams[1],
                    server.create_initialization_options(),
                )
            return Response()

        routes.append(Route(sse_path, endpoint=handle_sse, methods=["GET"]))
        routes.append(Mount(message_path, app=sse.handle_post_message))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        if session_manager is not None:
            async with session_manager.run():
                yield
        else:
            yield

    starlette_app = Starlette(routes=routes, lifespan=lifespan)
    mcp_paths = {path, sse_path, message_path.rstrip("/")}
    return _wrap_http_middleware(starlette_app, mcp_paths=mcp_paths)


def _run_network(**kwargs) -> None:
    import uvicorn
    app = _build_network_app(**{k: kwargs[k] for k in (
        "enable_http", "enable_sse", "path", "sse_path", "message_path", "json_response", "stateless"
    )})
    uvicorn.run(app, host=kwargs["host"], port=kwargs["port"], log_level="info")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gangtise-hub-api")
    parser.add_argument("--transport", choices=("http", "sse", "both"), default=os.getenv("MCP_TRANSPORT", "http"))
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8000")))
    parser.add_argument("--path", default=os.getenv("MCP_PATH", "/open-mcp"))
    parser.add_argument("--sse-path", default=os.getenv("MCP_SSE_PATH", "/sse"))
    parser.add_argument("--message-path", default=os.getenv("MCP_MESSAGE_PATH", "/messages/"))
    parser.add_argument("--stateless", action=argparse.BooleanOptionalAction,
                        default=os.getenv("MCP_STATELESS", "true").lower() in ("1", "true", "yes", "on"))
    parser.add_argument("--json-response", action=argparse.BooleanOptionalAction,
                        default=os.getenv("MCP_JSON_RESPONSE", "true").lower() in ("1", "true", "yes", "on"))
    return parser.parse_args(argv)


def _apply_network_gts_save_file_default() -> None:
    os.environ.setdefault("GTS_SAVE_FILE", "False")
    value = os.environ["GTS_SAVE_FILE"]
    for mod_name in (
        "gangtise_agent.utils", "gangtise_data.utils", "gangtise_file.utils",
        "gangtise_kb.utils", "gangtise_private.utils",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "GTS_SAVE_FILE"):
            setattr(mod, "GTS_SAVE_FILE", value)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    preload_all()
    _apply_network_gts_save_file_default()
    transport: Literal["http", "sse", "both"] = args.transport  # type: ignore[assignment]
    _run_network(
        host=args.host,
        port=args.port,
        enable_http=transport in ("http", "both"),
        enable_sse=transport in ("sse", "both"),
        path=args.path,
        sse_path=args.sse_path,
        message_path=args.message_path,
        json_response=args.json_response,
        stateless=args.stateless,
    )


if __name__ == "__main__":
    main()
