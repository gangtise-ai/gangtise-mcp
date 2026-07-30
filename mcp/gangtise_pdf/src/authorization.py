"""Gangtise API 鉴权（main）：支持直接 Authorization 与 AK/SK + loginV2。

优先级（get_authorization_token）：
1. 请求上下文 Authorization（HTTP 头透传）
2. 环境变量 GTS_AUTHORIZATION / AUTHORIZATION
3. 请求上下文 accessKey/secretKey → loginV2
4. 环境变量 / 本地文件中的 AK/SK 或 authorization
"""
from __future__ import annotations

import json
import time
import os
import stat
import threading
from contextvars import ContextVar, Token
from typing import Any, Dict, Optional, Tuple

import requests

GTS_ACCESS_KEY = os.getenv("GTS_ACCESS_KEY")
GTS_SECRET_KEY = os.getenv("GTS_SECRET_KEY")

DEPLOY_ENV = os.getenv("DEPLOY_ENV", "local")
if DEPLOY_ENV in ("prod", "local"):
    AUTHORIZATION_URL = "https://openapi.gangtise.com/application/auth/oauth/open/loginV2"
else:
    AUTHORIZATION_URL = "http://10.78.10.43:30901/application/auth/oauth/open/loginV2"

AUTH_EXPIRED_CODES = frozenset({"8000014", "8000013", 8000014, 8000013, "999002", 999002})
LOGIN_TIMEOUT = 30
# loginV2 未返回 expiresIn 时的默认缓存时长；刷新预留余量避免临界过期
TOKEN_DEFAULT_TTL_SEC = 3600.0
TOKEN_REFRESH_SKEW_SEC = 60.0

_lock = threading.Lock()
_session: Optional[Dict[str, Optional[str]]] = None

_request_authorization: ContextVar[Optional[str]] = ContextVar(
    "gts_request_authorization", default=None
)
_request_credentials: ContextVar[Optional[Tuple[str, str]]] = ContextVar(
    "gts_request_credentials", default=None
)
# 网关/上游可能注入的业务头：uid / tenantid / productcode
_request_headers_extra: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "gts_request_headers_extra", default=None
)
_request_sessions_lock = threading.Lock()
_request_sessions: Dict[Tuple[str, str], Dict[str, Optional[str]]] = {}

_EXTRA_HEADER_KEYS = ("uid", "tenantid", "productcode")


def get_authorization_path() -> str:
    """凭证文件路径：GTS_AUTHORIZATION_PATH > ~/.config/gangtise/authorization。"""
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


def set_request_credentials(access_key: str, secret_key: str) -> Token:
    """绑定当前异步/任务上下文的 ak/sk，返回 reset 用 token。"""
    ak = (access_key or "").strip()
    sk = (secret_key or "").strip()
    if not ak or not sk:
        raise ValueError("access_key 与 secret_key 均不能为空")
    return _request_credentials.set((ak, sk))


def reset_request_credentials(token: Token) -> None:
    _request_credentials.reset(token)


def get_request_credentials() -> Optional[Tuple[str, str]]:
    return _request_credentials.get()


def set_request_headers_extra(extra: Dict[str, str]) -> Token:
    """绑定当前请求要透传给下游的 uid/tenantid/productcode。"""
    cleaned: Dict[str, str] = {}
    for key in _EXTRA_HEADER_KEYS:
        raw = extra.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            cleaned[key] = value
    return _request_headers_extra.set(cleaned or None)


def reset_request_headers_extra(token: Token) -> None:
    _request_headers_extra.reset(token)


def get_request_headers_extra() -> Dict[str, str]:
    return dict(_request_headers_extra.get() or {})


def _login(ak: str, sk: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[float]]:
    payload = {"accessKey": ak, "secretKey": sk}
    try:
        response = requests.post(AUTHORIZATION_URL, json=payload, timeout=LOGIN_TIMEOUT)
    except requests.RequestException as exc:
        print(f"获取 authorization 失败, 网络错误: {exc}")
        return None, None, None, None, None
    if response.status_code != 200:
        print(f"获取 authorization 失败, HTTP {response.status_code}: {response.text}")
        return None, None, None, None, None
    try:
        body = response.json()
    except ValueError:
        print(f"获取 authorization 失败, 非 JSON 响应: {response.text}")
        return None, None, None, None, None
    # 新旧接口字段并存：历史用 state，当前 loginV2 用 status
    ok = body.get("state")
    if ok is None:
        ok = body.get("status", True)
    if not ok:
        print(f"获取 authorization 失败, 错误信息: {response.text}")
        return None, None, None, None, None
    try:
        data = body["data"]
        token = _ensure_bearer(str(data["accessToken"]))
        # loginV2 现可能只返回 accessToken / userName，不再带 uid/tenantId
        expires_at = _expires_at_from_login(data.get("expiresIn"))
        return (
            token,
            None if data.get("uid") is None else str(data.get("uid")),
            None if data.get("tenantId") is None else str(data.get("tenantId")),
            None if data.get("productCode") is None else str(data.get("productCode")),
            expires_at,
        )
    except (KeyError, TypeError) as exc:
        print(f"获取 authorization 失败, 响应格式异常: {exc}")
        return None, None, None, None, None


def _session_from_file() -> Optional[Dict[str, Optional[str]]]:
    path = get_authorization_path()
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        content = json.load(f)
    if content.get("authorization"):
        return {
            "authorization": _ensure_bearer(content["authorization"]),
            "uid": content.get("uid"),
            "tenantid": content.get("tenantId") or content.get("tenantid"),
            "productcode": str(content.get("productCode", content.get("productcode", ""))) or None,
        }
    ak = content.get("accessKey")
    sk = content.get("secretKey") or content.get("secretAccessKey")
    if ak and sk:
        token, uid, tenantid, productcode, expires_at = _login(ak, sk)
        if not token:
            return None
        return {
            "authorization": token,
            "uid": uid,
            "tenantid": tenantid,
            "productcode": productcode,
            "expires_at": expires_at,
        }
    return None


def invalidate_authorization() -> None:
    req = get_request_credentials()
    if req is not None:
        with _request_sessions_lock:
            _request_sessions.pop(req, None)
        return
    with _lock:
        global _session
        _session = None


def _session_fresh(session: Optional[Dict[str, Optional[str]]]) -> bool:
    """缓存会话是否仍可用（尊重 loginV2 expiresIn）。"""
    if not session or not session.get("authorization"):
        return False
    exp = session.get("expires_at")
    if exp is None:
        return True
    try:
        return time.time() < float(exp)
    except (TypeError, ValueError):
        return True


def _expires_at_from_login(expires_in: Any) -> Optional[float]:
    """loginV2 expiresIn（秒）→ 绝对过期时间；预留 60s 余量。无字段时默认 1h。"""
    ttl = TOKEN_DEFAULT_TTL_SEC
    if expires_in is not None:
        try:
            ttl = max(60.0, float(expires_in))
        except (TypeError, ValueError):
            pass
    return time.time() + ttl - TOKEN_REFRESH_SKEW_SEC


def _build_request_session(
    ak: str, sk: str, *, force: bool = False
) -> Optional[Dict[str, Optional[str]]]:
    key = (ak, sk)
    with _request_sessions_lock:
        cached = _request_sessions.get(key)
        if cached is not None and not force and _session_fresh(cached):
            return cached
    token, uid, tenantid, productcode, expires_at = _login(ak, sk)
    if not token:
        with _request_sessions_lock:
            _request_sessions.pop(key, None)
        return None
    session = {
        "authorization": token,
        "uid": uid,
        "tenantid": tenantid,
        "productcode": productcode,
        "expires_at": expires_at,
    }
    with _request_sessions_lock:
        _request_sessions[key] = session
    return session


def _build_session(force: bool = False) -> Optional[Dict[str, Optional[str]]]:
    req = get_request_credentials()
    if req is not None:
        return _build_request_session(req[0], req[1], force=force)

    global _session
    if _session is not None and not force and _session_fresh(_session):
        return _session
    ak = os.getenv("GTS_ACCESS_KEY") or GTS_ACCESS_KEY
    sk = os.getenv("GTS_SECRET_KEY") or GTS_SECRET_KEY
    if ak and sk:
        token, uid, tenantid, productcode, expires_at = _login(ak, sk)
        if not token:
            _session = None
            return None
        _session = {
            "authorization": token,
            "uid": uid,
            "tenantid": tenantid,
            "productcode": productcode,
            "expires_at": expires_at,
        }
        return _session
    file_session = _session_from_file()
    _session = file_session
    return _session


def _credentials_in_file(path: Optional[str] = None) -> bool:
    p = path or get_authorization_path()
    if not os.path.isfile(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if content.get("authorization"):
        return True
    ak = content.get("accessKey")
    sk = content.get("secretKey") or content.get("secretAccessKey")
    return bool(ak and sk)


def _env_authorization() -> Optional[str]:
    for key in ("GTS_AUTHORIZATION", "AUTHORIZATION"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            return _ensure_bearer(raw)
    return None


def is_auth_configured() -> bool:
    """请求头 Authorization / AK·SK / 环境变量 / 本地凭证文件是否已配置。"""
    if get_request_authorization():
        return True
    if _env_authorization():
        return True
    if get_request_credentials():
        return True
    if os.getenv("GTS_ACCESS_KEY") and os.getenv("GTS_SECRET_KEY"):
        return True
    if GTS_ACCESS_KEY and GTS_SECRET_KEY:
        return True
    return _credentials_in_file()


def save_credentials(access_key: str, secret_key: str, path: Optional[str] = None) -> str:
    """将 ak/sk 写入本地配置文件（权限 600），并清除内存中的 token 缓存。"""
    ak = (access_key or "").strip()
    sk = (secret_key or "").strip()
    if not ak or not sk:
        raise ValueError("access_key 与 secret_key 均不能为空")
    target = path or get_authorization_path()
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {"accessKey": ak, "secretKey": sk}
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    invalidate_authorization()
    return target


def clear_credentials(path: Optional[str] = None) -> Optional[str]:
    """删除本地凭证文件并清除内存 token 缓存。返回已删除路径；文件不存在则返回 None。"""
    target = path or get_authorization_path()
    removed: Optional[str] = None
    if os.path.isfile(target):
        os.remove(target)
        removed = target
    invalidate_authorization()
    return removed


def refresh_authorization(force: bool = True) -> Optional[Dict[str, Optional[str]]]:
    if get_request_authorization() is not None:
        return {"authorization": get_request_authorization()}
    if get_request_credentials() is not None:
        return _build_session(force=force)
    with _lock:
        return _build_session(force=force)


def get_authorization_token() -> Optional[str]:
    """解析当前可用 Authorization：直传优先，否则 AK/SK loginV2。"""
    direct = get_request_authorization()
    if direct:
        return direct
    env_token = _env_authorization()
    if env_token:
        return env_token
    if get_request_credentials() is not None:
        session = _build_session(force=False)
    else:
        with _lock:
            session = _build_session(force=False)
    if not session:
        return None
    return session.get("authorization")


def _session_headers_extra() -> Dict[str, str]:
    """从 loginV2 会话取 uid/tenantid/productcode（直传 Authorization 时通常没有）。"""
    if get_request_authorization() or _env_authorization():
        return {}
    if get_request_credentials() is not None:
        session = _build_session(force=False)
    else:
        with _lock:
            session = _build_session(force=False)
    if not session:
        return {}
    out: Dict[str, str] = {}
    if session.get("uid"):
        out["uid"] = str(session["uid"])
    if session.get("tenantid"):
        out["tenantid"] = str(session["tenantid"])
    if session.get("productcode"):
        out["productcode"] = str(session["productcode"])
    return out


def get_headers_extra() -> Dict[str, str]:
    """下游业务头：请求透传优先，其次 loginV2 会话。"""
    out = get_request_headers_extra()
    for key, value in _session_headers_extra().items():
        out.setdefault(key, value)
    return out


def is_authorization_passthrough() -> bool:
    """裸 Authorization 透传（未走请求级 AK/SK→loginV2）。

    云端若配置了内部 GANGTISE_*_DOMAIN，通常要求 uid；透传场景无 uid，
    应改用公网默认域名（见 resolve_gangtise_domain / gangtise_domain）。
    """
    if get_request_credentials() is not None:
        return False
    if get_request_authorization() is not None:
        return True
    if _env_authorization() is not None:
        return True
    return False


def resolve_gangtise_domain(env_key: str, default: str) -> str:
    """解析业务域名：Authorization 透传时忽略环境变量，固定返回 default。"""
    default_url = (default or "").strip().rstrip("/")
    if is_authorization_passthrough():
        return default_url
    raw = (os.getenv(env_key) or "").strip().rstrip("/")
    return raw or default_url


class GangtiseDomain:
    """惰性域名：请求时按鉴权模式解析。与路径用 ``+`` 拼接（勿用 f-string 赋模块常量）。"""

    __slots__ = ("env_key", "default")

    def __init__(self, env_key: str, default: str) -> None:
        self.env_key = env_key
        self.default = default

    def resolve(self) -> str:
        return resolve_gangtise_domain(self.env_key, self.default)

    def __str__(self) -> str:
        return self.resolve()

    def __repr__(self) -> str:
        return f"GangtiseDomain({self.env_key!r}, {self.default!r})"

    def __format__(self, spec: str) -> str:
        return format(self.resolve(), spec)

    def __add__(self, other: object) -> "GangtiseUrl":
        if not isinstance(other, str):
            return NotImplemented
        return GangtiseUrl(self, other)

    def __radd__(self, other: object) -> str:
        if not isinstance(other, str):
            return NotImplemented
        return other + self.resolve()

    def rstrip(self, *args: Any, **kwargs: Any) -> str:
        return self.resolve().rstrip(*args, **kwargs)


class GangtiseUrl:
    """惰性完整 URL（域名 + path），``str(url)`` / requests 调用时再解析。"""

    __slots__ = ("_domain", "_path")

    def __init__(self, domain: GangtiseDomain, path: str) -> None:
        self._domain = domain
        self._path = path if path.startswith("/") else f"/{path}"

    def resolve(self) -> str:
        return self._domain.resolve().rstrip("/") + self._path

    def __str__(self) -> str:
        return self.resolve()

    def __repr__(self) -> str:
        return f"GangtiseUrl({self._domain!r}, {self._path!r})"

    def __format__(self, spec: str) -> str:
        return format(self.resolve(), spec)

    def __add__(self, other: object) -> str:
        if not isinstance(other, str):
            return NotImplemented
        return self.resolve() + other

    def __radd__(self, other: object) -> str:
        if not isinstance(other, str):
            return NotImplemented
        return other + self.resolve()


def gangtise_domain(env_key: str, default: str) -> GangtiseDomain:
    """utils 中替代 ``os.getenv("GANGTISE_*_DOMAIN", default)``。"""
    return GangtiseDomain(env_key, default)


def rewrite_url_for_auth_mode(url: str) -> str:
    """Authorization 透传时，把命中的内部域名改写回公网 default（兜底）。"""
    if not url or not is_authorization_passthrough():
        return url
    text = str(url)
    # 常见 GANGTISE_*_DOMAIN：用当前 env 值匹配前缀，替换为公网默认
    for key, default in (
        ("GANGTISE_DATA_DOMAIN", "https://openapi.gangtise.com/application/open-data"),
        ("GANGTISE_INSIGHT_DOMAIN", "https://openapi.gangtise.com/application/open-insight"),
        ("GANGTISE_REFERENCE_DOMAIN", "https://openapi.gangtise.com/application/open-reference"),
        ("GANGTISE_OPENAI_DOMAIN", "https://openapi.gangtise.com/application/open-ai"),
        ("GANGTISE_QUOTE_DOMAIN", "https://openapi.gangtise.com/application/open-quote"),
        ("GANGTISE_FUNDAMENTAL_DOMAIN", "https://openapi.gangtise.com/application/open-fundamental"),
        ("GANGTISE_ALTERNATIVE_DOMAIN", "https://openapi.gangtise.com/application/open-alternative"),
        ("GANGTISE_INDICATOR_DOMAIN", "https://openapi.gangtise.com/application/open-indicator"),
        ("GANGTISE_VAULT_DOMAIN", "https://openapi.gangtise.com/application/open-vault"),
        ("GANGTISE_TOOL_DOMAIN", "https://openapi.gangtise.com/application/open-tool"),
    ):
        internal = (os.getenv(key) or "").strip().rstrip("/")
        public = default.rstrip("/")
        if not internal or internal == public:
            continue
        if text == internal or text.startswith(internal + "/"):
            return public + text[len(internal) :]
    return text


def get_authorization_headers() -> Dict[str, str]:
    token = get_authorization_token()
    if not token:
        return dict(get_headers_extra())
    return {"Authorization": token, **get_headers_extra()}


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
    error_type = str(data.get("errorType") or data.get("error_type") or "").strip().upper()
    if error_type in {"TOKEN_INVALID", "CREDENTIAL_INVALID", "UNAUTHORIZED"}:
        return True
    for key in ("code", "errorCode", "errCode", "state", "error"):
        val = data.get(key)
        if val in AUTH_EXPIRED_CODES or str(val) in {str(c) for c in AUTH_EXPIRED_CODES}:
            return True
    msg = str(data.get("message") or data.get("msg") or "")
    if msg and any(k in msg for k in ("8000014", "8000013", "令牌", "token", "Token", "登录", "授权")):
        if any(k in msg for k in ("过期", "失效", "无效", "expired", "invalid", "未登录")):
            return True
    return False


def authorized_request(method: str, url: str, *, retry_on_auth: bool = True, **kwargs) -> requests.Response:
    """带鉴权头发起请求；鉴权失败时强制刷新 token 并重试一次（直传 Authorization 不重登）。"""
    headers = dict(kwargs.pop("headers", {}) or {})
    timeout = kwargs.pop("timeout", 120)
    headers.update(get_authorization_headers())
    target = rewrite_url_for_auth_mode(str(url))
    response = requests.request(method, target, headers=headers, timeout=timeout, **kwargs)
    if retry_on_auth and is_auth_error_response(response):
        # 直传 token 无法通过 loginV2 刷新
        if get_request_authorization() or _env_authorization():
            return response
        try:
            body = response.json()
        except ValueError:
            body = None
        if is_auth_error_response(response, body):
            invalidate_authorization()
            refresh_authorization(force=True)
            headers.update(get_authorization_headers())
            response = requests.request(method, target, headers=headers, timeout=timeout, **kwargs)
    return response
