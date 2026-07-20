"""按 slug 将 /open-mcp|/sse|/messages 反代到各 MCP 子进程。"""
from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from typing import Dict, Iterable, Optional

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from http_compat import (
    DASHSCOPE_REQUEST_ID,
    resolve_request_id,
)
from services import ALL_SERVICES

DEFAULT_BACKENDS = {s.slug: s.port for s in ALL_SERVICES}

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


def _parse_backends(raw: Optional[str]) -> Dict[str, int]:
    if not raw:
        return dict(DEFAULT_BACKENDS)
    out: Dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        slug, _, port_s = part.partition("=")
        out[slug.strip()] = int(port_s.strip())
    return out or dict(DEFAULT_BACKENDS)


def _resolve_slug(path: str, slugs: Iterable[str]) -> Optional[str]:
    normalized = path if path.startswith("/") else f"/{path}"
    for prefix in ("/open-mcp/", "/sse/", "/messages/"):
        if not normalized.startswith(prefix):
            continue
        rest = normalized[len(prefix) :]
        if not rest:
            return None
        slug = rest.split("/", 1)[0]
        if slug in slugs:
            return slug
    return None


def create_app(backends: Dict[str, int]) -> Starlette:
    timeout = httpx.Timeout(None)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "layout": "gateway",
                "services": sorted(backends.keys()),
                "endpoints": {
                    slug: {
                        "mcp": f"/open-mcp/{slug}",
                        "sse": f"/sse/{slug}",
                        "messages": f"/messages/{slug}/",
                    }
                    for slug in sorted(backends)
                },
            }
        )

    async def proxy(request: Request) -> Response:
        slug = _resolve_slug(request.url.path, backends.keys())
        request_id = resolve_request_id({k.lower(): v for k, v in request.headers.items()})
        if slug is None:
            return JSONResponse(
                {
                    "error": "not_found",
                    "message": (
                        "未知路径。请使用 /open-mcp/{slug}、/sse/{slug}、/messages/{slug}/，"
                        f"slug 为: {', '.join(sorted(backends))}"
                    ),
                },
                status_code=404,
                headers={DASHSCOPE_REQUEST_ID: request_id},
            )

        port = backends[slug]
        target = request.url.replace(hostname="127.0.0.1", port=port, scheme="http")
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        headers[DASHSCOPE_REQUEST_ID] = request_id

        body = await request.body()
        client: httpx.AsyncClient = request.app.state.http_client

        req = client.build_request(
            request.method,
            str(target),
            headers=headers,
            content=body if body else None,
        )
        upstream = await client.send(req, stream=True)

        resp_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        resp_headers[DASHSCOPE_REQUEST_ID] = (
            resp_headers.get(DASHSCOPE_REQUEST_ID)
            or resp_headers.get("X-DashScope-Request-ID")
            or request_id
        )

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            body_iter(),
            status_code=upstream.status_code,
            headers=resp_headers,
        )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
        try:
            yield
        finally:
            await app.state.http_client.aclose()

    return Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Route(
                "/{path:path}",
                endpoint=proxy,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
            ),
        ],
        lifespan=lifespan,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gangtise MCP gateway（多 slug 反代）")
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8000")))
    parser.add_argument(
        "--backends",
        default=os.getenv("MCP_GATEWAY_BACKENDS", ""),
        help="slug=port 列表，逗号分隔，如 agent=18001,data=18002",
    )
    args = parser.parse_args(argv)
    backends = _parse_backends(args.backends)
    app = create_app(backends)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
