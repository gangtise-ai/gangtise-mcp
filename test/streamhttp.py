#!/usr/bin/env python3
"""Streamable HTTP MCP smoke：initialize → notifications/initialized → tools/list。

用法：
  python3 test/streamhttp.py
  python3 test/streamhttp.py --url http://127.0.0.1:8000/open-mcp/
  python3 test/streamhttp.py --url https://openapi.gangtise.com/application/mcp/
  python3 test/streamhttp.py --url https://test-open.gangtise.com.cn/application/open-mcp/

鉴权（任选其一）：
  export GTS_ACCESS_KEY=... GTS_SECRET_KEY=...
  export MCP_TOKEN=...          # 或 --token / Authorization Bearer
  --header 'accessKey: ...' --header 'secretKey: ...'

依赖：pip install requests
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

DEFAULT_URL = os.getenv("MCP_URL", "http://127.0.0.1:8000/open-mcp/")


def _ok(name: str, detail: str = "") -> None:
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str) -> None:
    print(f"  FAIL  {name} — {detail}", file=sys.stderr)


def _parse_header(raw: str) -> Tuple[str, str]:
    if ":" not in raw:
        raise argparse.ArgumentTypeError(f"header 须为 'Name: value'，收到: {raw!r}")
    name, value = raw.split(":", 1)
    return name.strip(), value.strip()


def _auth_headers(args: argparse.Namespace) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for name, value in args.header or []:
        headers[name] = value

    token = (args.token or os.getenv("MCP_TOKEN") or os.getenv("GTS_AUTHORIZATION") or "").strip()
    if token:
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers.setdefault("Authorization", token)

    ak = (os.getenv("GTS_ACCESS_KEY") or "").strip()
    sk = (os.getenv("GTS_SECRET_KEY") or "").strip()
    if ak and sk:
        headers.setdefault("accessKey", ak)
        headers.setdefault("secretKey", sk)

    return headers


def _parse_jsonrpc_body(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # SSE: data: {...}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    return None


def _post_jsonrpc(
    session: requests.Session,
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Dict[str, str],
    session_id: Optional[str] = None,
    timeout: int = 60,
) -> Tuple[requests.Response, Optional[Dict[str, Any]]]:
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **headers,
    }
    if session_id:
        req_headers["mcp-session-id"] = session_id
    resp = session.post(url, json=payload, headers=req_headers, timeout=timeout)
    return resp, _parse_jsonrpc_body(resp.text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Streamable HTTP MCP smoke 测试")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"MCP streamable-http 地址（默认 {DEFAULT_URL}，可用 MCP_URL）",
    )
    parser.add_argument("--token", default="", help="Bearer token（也可用 MCP_TOKEN）")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        type=_parse_header,
        metavar="Name:\\ value",
        help="额外请求头，可重复；如 --header 'accessKey: xxx'",
    )
    parser.add_argument(
        "--skip-tools-list",
        action="store_true",
        help="只做 initialize，不调用 tools/list",
    )
    parser.add_argument("--timeout", type=int, default=60, help="单次请求超时秒数")
    args = parser.parse_args()

    url = args.url.strip()
    if not url:
        print("--url 不能为空", file=sys.stderr)
        return 2

    headers = _auth_headers(args)
    session = requests.Session()
    print(f"url={url}")
    print(f"auth_headers={sorted(k for k in headers if k.lower() != 'authorization') or '(none)'}")
    if "Authorization" in headers:
        print("auth_headers+=Authorization")
    print()

    print("[1] initialize")
    resp, body = _post_jsonrpc(
        session,
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcps-test-streamhttp", "version": "0.1.0"},
            },
        },
        headers=headers,
        timeout=args.timeout,
    )
    if resp.status_code == 401:
        _fail("initialize", "HTTP 401（请配置 AK/SK、--token 或 --header）")
        return 1
    if resp.status_code != 200:
        _fail("initialize", f"HTTP {resp.status_code}: {resp.text[:400]}")
        return 1

    session_id = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
    if not body or "result" not in body:
        _fail("initialize", f"无 result: {(resp.text or '')[:300]}")
        return 1
    server = (body.get("result") or {}).get("serverInfo") or {}
    _ok(
        "initialize",
        f"server={server.get('name', '?')} session={session_id or '(none)'}",
    )

    print("[2] notifications/initialized")
    note_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **headers,
    }
    if session_id:
        note_headers["mcp-session-id"] = session_id
    note = session.post(
        url,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=note_headers,
        timeout=args.timeout,
    )
    # 部分实现返回 202/204/200；只要不是 4xx/5xx 即视为通过
    if note.status_code >= 400:
        _fail("initialized", f"HTTP {note.status_code}: {note.text[:300]}")
        return 1
    _ok("initialized", f"HTTP {note.status_code}")

    if args.skip_tools_list:
        print()
        print("全部通过（已跳过 tools/list）。")
        return 0

    print("[3] tools/list")
    resp, body = _post_jsonrpc(
        session,
        url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
        session_id=session_id,
        timeout=args.timeout,
    )
    if resp.status_code != 200:
        _fail("tools/list", f"HTTP {resp.status_code}: {resp.text[:400]}")
        return 1
    if not body or "result" not in body:
        _fail("tools/list", f"无 result: {(resp.text or '')[:300]}")
        return 1
    tools: List[Any] = (body.get("result") or {}).get("tools") or []
    names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    preview = ", ".join(names[:8]) + ("…" if len(names) > 8 else "")
    _ok("tools/list", f"{len(names)} tools" + (f" ({preview})" if preview else ""))

    print()
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
