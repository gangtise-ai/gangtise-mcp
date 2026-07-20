"""Gangtise MCP HTTP/SSE 整合入口。

- --layout unified：单进程 tools/list = 五域全部叶子工具
- --layout gateway：拉起各域 api 子进程 + 网关反代

仅 `--transport http|sse|both`（默认 both）。stdio 请用 mcp 包 gangtise-mcp。
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import inspect
import io
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager, redirect_stdout
from contextvars import copy_context
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple


def _ensure_layer_paths() -> None:
    here = Path(__file__).resolve().parent
    paths = [here]
    if here.name == "src":
        mcps_root = here.parents[2]
        paths.append(mcps_root / "mcp" / "gangtise_mcp" / "src")
        for dom in (
            "gangtise_agent",
            "gangtise_data",
            "gangtise_file",
            "gangtise_kb",
            "gangtise_private",
        ):
            paths.append(mcps_root / "mcp" / dom / "src")
            paths.append(mcps_root / "api" / dom / "src")
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
from http_compat import HttpMiddleware
from http_gateway import main as gateway_main
from result_attachments import with_path_attachments
from services import (
    backends_csv,
    invoke_package_main,
    mcp_root,
    resolve_package,
    run_package_stdio,
    select_services,
    start_backend,
    wait_port,
)
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
    attach = True
    if os.getenv("MCP_ATTACH_FILES", "").lower() in ("0", "false", "no", "off"):
        attach = False
    return with_path_attachments(text, enabled=attach)




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
    keys = ("enable_http", "enable_sse", "path", "sse_path", "message_path", "json_response", "stateless")
    app = _build_network_app(**{k: kwargs[k] for k in keys})
    uvicorn.run(app, host=kwargs["host"], port=kwargs["port"], log_level="info")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gangtise-mcp-api")
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
    parser.add_argument(
        "--layout",
        choices=("unified", "gateway"),
        default=None,
        help="unified=单进程全量工具；gateway=子进程+反代（Docker 常用）",
    )
    parser.add_argument(
        "--package",
        default=os.getenv("MCP_PACKAGE", "domains"),
        help="gateway 时选择后端：domains|all|agent|data|...",
    )
    return parser.parse_args(argv)


def _resolve_layout(args: argparse.Namespace) -> str:
    if args.layout:
        return args.layout
    env_layout = (os.getenv("MCP_LAYOUT") or "").strip()
    if env_layout == "hub":
        return "unified"
    if env_layout in ("unified", "gateway"):
        return env_layout
    return "unified"


def _delegate_argv(args: argparse.Namespace, transport: str) -> List[str]:
    argv = [
        "--transport", transport,
        "--host", args.host,
        "--port", str(args.port),
        "--path", args.path,
        "--sse-path", args.sse_path,
        "--message-path", args.message_path,
    ]
    argv.append("--stateless" if args.stateless else "--no-stateless")
    argv.append("--json-response" if args.json_response else "--no-json-response")
    return argv


def _preload_or_warn() -> None:
    cat = load_catalog()
    if cat.errors:
        for pkg, err in cat.errors.items():
            print(f"[gangtise-mcp] 警告：未加载 {pkg}: {err}", file=sys.stderr)
    print(
        f"[gangtise-mcp] 已挂载 {len(cat.handlers)} 个叶子工具"
        f"（来自 {', '.join(DOMAIN_PACKAGES)}）",
        file=sys.stderr,
    )


def _cleanup_children(children: List[subprocess.Popen]) -> None:
    for proc in children:
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except OSError:
                pass
    for proc in children:
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass


def _run_gateway(args: argparse.Namespace, transport: str) -> None:
    os.environ.setdefault("GTS_MCP_ROOT", str(mcp_root()))
    os.environ.setdefault("GTS_SAVE_FILE", "False")

    try:
        services = select_services(args.package)
    except KeyError:
        print(
            f"未知 --package={args.package}。"
            "可选: domains | all | agent | data | file | kb | private | hub | mcp | gangtise_*",
            file=sys.stderr,
        )
        raise SystemExit(1)

    children: List[subprocess.Popen] = []
    atexit.register(lambda: _cleanup_children(children))

    for spec in services:
        proc = start_backend(spec, transport=transport)
        children.append(proc)
        print(
            f"[gangtise-mcp] started {spec.slug} -> 127.0.0.1:{spec.port} "
            f"(/open-mcp/{spec.slug})",
            file=sys.stderr,
        )

    for spec in services:
        try:
            wait_port(spec.port, timeout_s=40.0)
        except TimeoutError as e:
            _cleanup_children(children)
            print(str(e), file=sys.stderr)
            raise SystemExit(1) from e

    gateway_main(
        [
            "--host", args.host,
            "--port", str(args.port),
            "--backends", backends_csv(services),
        ]
    )


def _apply_network_gts_save_file_default() -> None:
    os.environ.setdefault("GTS_SAVE_FILE", "False")
    value = os.environ["GTS_SAVE_FILE"]
    for mod_name in (
        "gangtise_agent.utils",
        "gangtise_data.utils",
        "gangtise_file.utils",
        "gangtise_kb.utils",
        "gangtise_private.utils",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "GTS_SAVE_FILE"):
            setattr(mod, "GTS_SAVE_FILE", value)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    transport: Literal["http", "sse", "both"] = args.transport  # type: ignore[assignment]
    layout = _resolve_layout(args)

    # 调试：单域委托
    pkg = (args.package or "").strip()
    if pkg and pkg not in ("domains", "five", "core", "all", "*", "mcp", "gangtise_mcp") and layout == "unified":
        try:
            spec = resolve_package(pkg)
            invoke_package_main(spec.package_dir, _delegate_argv(args, transport))
            return
        except KeyError:
            pass

    if layout == "gateway":
        _run_gateway(args, transport)
        return

    _apply_network_gts_save_file_default()
    _preload_or_warn()
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
