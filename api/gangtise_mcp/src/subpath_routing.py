"""MCP 来源子路径：SUBPATHS 与 MCP_PATH 组合多入口，统一落到同一 MCP 服务。

示例：MCP_PATH=/open-mcp，SUBPATHS=doubao,ali,comate
  POST /open-mcp/          → 内部 /open-mcp（source 为空）
  POST /open-mcp/doubao/   → 内部 /open-mcp（source=doubao）
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable, Iterable, Optional, Set, Tuple


def parse_subpaths(raw: Optional[str] = None) -> Tuple[str, ...]:
    """解析 SUBPATHS 环境变量，返回去重后的子路径名（不含斜杠）。"""
    text = raw if raw is not None else os.getenv("SUBPATHS", "")
    seen: set[str] = set()
    out: list[str] = []
    for part in (text or "").split(","):
        name = part.strip().strip("/")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _normalize_base(path: str) -> str:
    p = (path or "/").strip() or "/"
    if not p.startswith("/"):
        p = f"/{p}"
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def split_subpath(
    path: str,
    *,
    mcp_base: str,
    subpaths: Iterable[str],
) -> Tuple[str, Optional[str]]:
    """从请求路径剥离来源子路径，返回 (改写后的 path, source 或 None)。"""
    p = path or "/"
    base = _normalize_base(mcp_base)
    subs: Set[str] = {s.strip().strip("/") for s in subpaths if s.strip().strip("/")}
    if not subs:
        return p, None

    if base == "/":
        for sp in sorted(subs, key=len, reverse=True):
            prefix = f"/{sp}"
            if p == prefix:
                return "/", sp
            if p.startswith(prefix + "/"):
                rest = p[len(prefix) :]
                return rest if rest.startswith("/") else f"/{rest}", sp
        return p, None

    if p != base and not p.startswith(base + "/"):
        return p, None

    rest = p[len(base) :]
    if not rest or rest == "/":
        return base, None

    tail = rest.lstrip("/")
    head, _, suffix = tail.partition("/")
    if head not in subs:
        return p, None

    if suffix:
        stripped = f"{base}/{suffix}"
    else:
        stripped = base
    return stripped, head


def _parse_mcp_body(body: bytes) -> Tuple[Optional[str], Optional[str]]:
    """从 streamable HTTP JSON 体解析 mcp_method 与 tool 名。"""
    if not body:
        return None, None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None

    mcp_method = payload.get("method")
    tool: Optional[str] = None
    if mcp_method == "tools/call":
        params = payload.get("params")
        if isinstance(params, dict):
            name = params.get("name")
            if name is not None:
                tool = str(name)
    return (str(mcp_method) if mcp_method is not None else None), tool


def log_mcp_access(
    *,
    source: Optional[str],
    path: str,
    method: str,
    request_id: str,
    mcp_method: Optional[str] = None,
    tool: Optional[str] = None,
) -> None:
    record = {
        "event": "mcp_access",
        "source": source or "",
        "path": path,
        "method": method,
        "request_id": request_id,
    }
    if mcp_method:
        record["mcp_method"] = mcp_method
    if tool:
        record["tool"] = tool
    print(f"[mcp-access] {json.dumps(record, ensure_ascii=False)}", file=sys.stderr, flush=True)


class _BodyReplayReceive:
    """缓存已读 body，供下游 ASGI 再次读取。"""

    def __init__(self, receive, body: bytes) -> None:
        self._receive = receive
        self._body = body
        self._consumed = False

    async def __call__(self):
        if not self._consumed:
            self._consumed = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        return await self._receive()


class SubpathMiddleware:
    """剥离 MCP_PATH 下的来源子路径，并输出结构化访问日志。"""

    def __init__(
        self,
        app,
        *,
        mcp_base: str,
        subpaths: Optional[Iterable[str]] = None,
        should_log: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self.app = app
        self.mcp_base = _normalize_base(mcp_base)
        parsed = parse_subpaths(",".join(subpaths)) if subpaths else parse_subpaths()
        self.subpaths = parsed
        self._should_log = should_log or self._default_should_log

    @staticmethod
    def _default_should_log(method: str, path: str) -> bool:
        if method.upper() not in ("POST", "GET", "PUT", "PATCH", "DELETE"):
            return False
        skip_prefixes = ("/.well-known/", "/oauth/", "/health")
        if path in ("/health", "/authorize", "/token", "/register"):
            return False
        for prefix in skip_prefixes:
            if path.startswith(prefix):
                return False
        return True

    async def __call__(self, scope, receive, send) -> None:
        from http_compat import headers_from_scope, resolve_request_id

        if scope["type"] != "http" or not self.subpaths:
            await self.app(scope, receive, send)
            return

        original_path = scope.get("path") or "/"
        stripped, source = split_subpath(
            original_path,
            mcp_base=self.mcp_base,
            subpaths=self.subpaths,
        )

        headers = headers_from_scope(scope)
        request_id = resolve_request_id(headers)
        method = (scope.get("method") or "GET").upper()

        body = b""
        if method in ("POST", "PUT", "PATCH"):
            chunks: list[bytes] = []
            while True:
                message = await receive()
                chunk = message.get("body") or b""
                if chunk:
                    chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            receive = _BodyReplayReceive(receive, body)

        if stripped != original_path:
            scope = {**scope, "path": stripped}
            raw = scope.get("raw_path")
            if isinstance(raw, (bytes, bytearray)):
                scope = {**scope, "raw_path": stripped.encode("latin-1")}

        scope = {
            **scope,
            "state": {
                **(scope.get("state") or {}),
                "mcp_source": source or "",
                "mcp_original_path": original_path,
            },
        }

        if self._should_log(method, original_path):
            mcp_method, tool = _parse_mcp_body(body)
            log_mcp_access(
                source=source,
                path=original_path,
                method=method,
                request_id=request_id,
                mcp_method=mcp_method,
                tool=tool,
            )

        await self.app(scope, receive, send)
