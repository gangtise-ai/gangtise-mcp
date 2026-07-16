"""百炼 HTTP 兼容：回传 X-DashScope-Request-ID，透传入站 Authorization。"""
from __future__ import annotations

import json
import os
import uuid
from typing import Callable, Dict, Optional, Set

from starlette.types import ASGIApp, Receive, Scope, Send

DASHSCOPE_REQUEST_ID = "x-dashscope-request-id"

_AUTH_SKIP_PREFIXES = (
    "/.well-known/",
    "/authorize",
    "/token",
    "/register",
    "/health",
)


def env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def require_auth_enabled() -> bool:
    return env_flag("MCP_REQUIRE_AUTH", "true")


def headers_from_scope(scope: Scope) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        out[key.decode("latin-1").lower()] = value.decode("latin-1")
    return out


def resolve_request_id(headers: Dict[str, str]) -> str:
    rid = (headers.get(DASHSCOPE_REQUEST_ID) or "").strip()
    return rid or str(uuid.uuid4())


def path_skips_auth(path: str) -> bool:
    p = path or "/"
    for prefix in _AUTH_SKIP_PREFIXES:
        if p == prefix.rstrip("/") or p.startswith(prefix):
            return True
    return False


async def send_json_status(
    send: Send,
    status: int,
    body: dict,
    *,
    request_id: str,
) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (DASHSCOPE_REQUEST_ID.encode("latin-1"), request_id.encode("latin-1")),
                (b"content-length", str(len(payload)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def wrap_send_with_request_id(send: Send, request_id: str) -> Send:
    async def _send(message: dict) -> None:
        if message.get("type") == "http.response.start":
            headers = list(message.get("headers") or [])
            headers = [
                (k, v)
                for k, v in headers
                if k.decode("latin-1").lower() != DASHSCOPE_REQUEST_ID
            ]
            headers.append(
                (DASHSCOPE_REQUEST_ID.encode("latin-1"), request_id.encode("latin-1"))
            )
            message = {**message, "headers": headers}
        await send(message)

    return _send


class BailianHttpMiddleware:
    """回传 X-DashScope-Request-ID；将入站 Authorization 注入请求上下文。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        set_authorization: Callable[[str], object],
        reset_authorization: Callable[[object], None],
        mcp_paths: Optional[Set[str]] = None,
    ) -> None:
        self.app = app
        self.set_authorization = set_authorization
        self.reset_authorization = reset_authorization
        self.mcp_paths = mcp_paths or set()

    def _is_mcp_path(self, path: str) -> bool:
        if path_skips_auth(path):
            return False
        if not self.mcp_paths:
            return not path_skips_auth(path)
        for p in self.mcp_paths:
            if path == p or path.startswith(p.rstrip("/") + "/") or path.startswith(p):
                return True
        for prefix in ("/mcp", "/sse", "/messages"):
            if path == prefix or path.startswith(prefix + "/"):
                return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = headers_from_scope(scope)
        request_id = resolve_request_id(headers)
        send = wrap_send_with_request_id(send, request_id)

        path = scope.get("path") or "/"
        auth = (headers.get("authorization") or "").strip()
        token = None
        if auth:
            token = self.set_authorization(auth)

        if require_auth_enabled() and self._is_mcp_path(path) and not auth:
            await send_json_status(
                send,
                401,
                {
                    "error": "unauthorized",
                    "message": "Missing Authorization header",
                    "code": "UNAUTHORIZED",
                },
                request_id=request_id,
            )
            return

        try:
            await self.app(scope, receive, send)
        finally:
            if token is not None:
                self.reset_authorization(token)
