"""HTTP 兼容中间件：回传 X-DashScope-Request-ID；注入 Authorization 或 AK/SK。"""
from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Callable, Dict, Optional, Set, Tuple

from starlette.types import ASGIApp, Receive, Scope, Send

DASHSCOPE_REQUEST_ID = "x-dashscope-request-id"

_AUTH_SKIP_PREFIXES = (
    "/.well-known/",
    "/authorize",
    "/token",
    "/register",
    "/health",
)

_CREDENTIALS_HEADER_NAMES = (
    "x-gts-credentials",
    "gts-credentials",
    "x-gangtise-credentials",
)

# 上游网关可能注入、需透传给下游 OpenAPI 的业务头
_FORWARD_EXTRA_HEADER_KEYS = (
    "uid",
    "tenantid",
    "productcode",
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


def parse_credentials_payload(raw: str) -> Optional[Tuple[str, str]]:
    text = (raw or "").strip()
    if not text:
        return None
    if not text.startswith("{"):
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
            if decoded.strip().startswith("{"):
                text = decoded.strip()
        except Exception:
            pass
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ak = data.get("accessKey") or data.get("access_key")
    sk = data.get("secretKey") or data.get("secret_key") or data.get("secretAccessKey")
    if ak and sk:
        return str(ak).strip(), str(sk).strip()
    return None


def parse_credentials_from_headers(headers: Dict[str, str]) -> Optional[Tuple[str, str]]:
    for name in _CREDENTIALS_HEADER_NAMES:
        if name in headers:
            parsed = parse_credentials_payload(headers[name])
            if parsed:
                return parsed
    ak = headers.get("accesskey") or headers.get("x-access-key") or headers.get("access-key")
    sk = (
        headers.get("secretkey")
        or headers.get("x-secret-key")
        or headers.get("secret-key")
        or headers.get("secretaccesskey")
    )
    if ak and sk:
        return ak.strip(), sk.strip()
    return None


def parse_headers_extra_from_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """从入站请求提取 uid / tenantid / productcode（大小写不敏感，已 lower）。"""
    out: Dict[str, str] = {}
    for key in _FORWARD_EXTRA_HEADER_KEYS:
        raw = headers.get(key) or headers.get(f"x-{key}")
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            out[key] = value
    return out


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


class HttpMiddleware:
    """回传 Request-ID；注入 Authorization 或 AK/SK；透传 uid/tenantid/productcode。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        set_authorization: Callable[[str], object],
        reset_authorization: Callable[[object], None],
        set_credentials: Optional[Callable[[str, str], object]] = None,
        reset_credentials: Optional[Callable[[object], None]] = None,
        set_headers_extra: Optional[Callable[[Dict[str, str]], object]] = None,
        reset_headers_extra: Optional[Callable[[object], None]] = None,
        mcp_paths: Optional[Set[str]] = None,
    ) -> None:
        self.app = app
        self.set_authorization = set_authorization
        self.reset_authorization = reset_authorization
        self.set_credentials = set_credentials
        self.reset_credentials = reset_credentials
        self.set_headers_extra = set_headers_extra
        self.reset_headers_extra = reset_headers_extra
        self.mcp_paths = mcp_paths or set()

    def _is_mcp_path(self, path: str) -> bool:
        if path_skips_auth(path):
            return False
        if not self.mcp_paths:
            return not path_skips_auth(path)
        for p in self.mcp_paths:
            if path == p or path.startswith(p.rstrip("/") + "/") or path.startswith(p):
                return True
        for prefix in ("/open-mcp", "/sse", "/messages"):
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
        creds = None if auth else parse_credentials_from_headers(headers)
        extra = parse_headers_extra_from_headers(headers)

        auth_token = None
        cred_token = None
        extra_token = None
        if auth:
            auth_token = self.set_authorization(auth)
        elif creds and self.set_credentials is not None:
            cred_token = self.set_credentials(creds[0], creds[1])
        if extra and self.set_headers_extra is not None:
            extra_token = self.set_headers_extra(extra)

        if require_auth_enabled() and self._is_mcp_path(path) and not auth and not creds:
            await send_json_status(
                send,
                401,
                {
                    "error": "unauthorized",
                    "message": "Missing Authorization or credentials headers",
                    "code": "UNAUTHORIZED",
                },
                request_id=request_id,
            )
            return

        try:
            await self.app(scope, receive, send)
        finally:
            if auth_token is not None:
                self.reset_authorization(auth_token)
            if cred_token is not None and self.reset_credentials is not None:
                self.reset_credentials(cred_token)
            if extra_token is not None and self.reset_headers_extra is not None:
                self.reset_headers_extra(extra_token)
