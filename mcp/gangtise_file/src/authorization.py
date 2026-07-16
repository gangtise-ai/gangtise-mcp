"""Gangtise API 鉴权（aliyun）：透传 Authorization，不再使用 AK/SK + loginV2。

HTTP：中间件把入站 Authorization 写入 ContextVar，下游请求原样携带。
stdio：可用环境变量 GTS_AUTHORIZATION / AUTHORIZATION，或本地文件中的 authorization 字段。
"""
from __future__ import annotations

import json
import os
from contextvars import ContextVar, Token
from typing import Any, Dict, Optional

import requests

AUTH_EXPIRED_CODES = frozenset({"8000014", "8000013", 8000014, 8000013})

_request_authorization: ContextVar[Optional[str]] = ContextVar(
    "gts_request_authorization", default=None
)


def get_authorization_path() -> str:
    explicit = os.getenv("GTS_AUTHORIZATION_PATH")
    if explicit:
        return os.path.expanduser(explicit)
    return os.path.join(os.path.expanduser("~"), ".config", "gangtise", "authorization")


GTS_AUTHORIZATION_PATH = get_authorization_path()


def _ensure_bearer(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def set_request_authorization(authorization: str) -> Token:
    """绑定当前请求的 Authorization 头（可含 Bearer 前缀）。"""
    value = _ensure_bearer(authorization)
    if not value:
        raise ValueError("authorization 不能为空")
    return _request_authorization.set(value)


def reset_request_authorization(token: Token) -> None:
    _request_authorization.reset(token)


def get_request_authorization() -> Optional[str]:
    return _request_authorization.get()


def _authorization_from_env() -> Optional[str]:
    raw = (
        os.getenv("GTS_AUTHORIZATION")
        or os.getenv("AUTHORIZATION")
        or ""
    ).strip()
    return _ensure_bearer(raw) or None


def _authorization_from_file() -> Optional[str]:
    path = get_authorization_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(content, dict):
        return None
    raw = content.get("authorization") or content.get("Authorization") or ""
    return _ensure_bearer(str(raw)) or None


def is_auth_configured() -> bool:
    if get_request_authorization():
        return True
    if _authorization_from_env():
        return True
    return bool(_authorization_from_file())


def get_authorization_token() -> Optional[str]:
    return (
        get_request_authorization()
        or _authorization_from_env()
        or _authorization_from_file()
    )


def get_headers_extra() -> Dict[str, str]:
    """透传模式下无 loginV2 会话信息。"""
    return {}


def get_authorization_headers() -> Dict[str, str]:
    token = get_authorization_token()
    if not token:
        return {}
    return {"Authorization": token}


def invalidate_authorization() -> None:
    """透传模式下无缓存可清。"""
    return


def refresh_authorization(force: bool = True) -> Optional[Dict[str, Optional[str]]]:
    token = get_authorization_token()
    if not token:
        return None
    return {"authorization": token}


def is_auth_error_response(
    response: Optional[requests.Response] = None,
    payload: Any = None,
) -> bool:
    if response is not None and response.status_code == 401:
        return True
    data = payload
    if data is None and response is not None:
        try:
            data = response.json()
        except ValueError:
            return False
    if not isinstance(data, dict):
        return False
    code = str(data.get("code", "")).strip()
    if code in {str(c) for c in AUTH_EXPIRED_CODES}:
        return True
    return False


def authorized_request(method: str, url: str, *, retry_on_auth: bool = True, **kwargs) -> requests.Response:
    """带 Authorization 发起请求（透传模式不做自动重登）。"""
    headers = dict(kwargs.pop("headers", {}) or {})
    timeout = kwargs.pop("timeout", 120)
    headers.update(get_authorization_headers())
    return requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
