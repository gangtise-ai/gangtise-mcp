"""MCP OAuth 2.1 Authorization Server（WorkBuddy / Cursor 等客户端）。

环境变量：
  GTS_JWT_SECRET     — 必填（启用 OAuth）；任意长度字符串，内部 SHA-256 后作 HMAC 密钥
  GTS_CRED_ENC_KEY   — 可选；Fernet key，缺省由 JWT secret 派生
  GTS_OAUTH_ISSUER   — 对外 issuer（反代后完整 URL，无尾斜杠）
                       例：https://openapi.gangtise.com/application/open-mcp
  GTS_OAUTH_ACCESS_TTL   — access 秒数，默认 3600
  GTS_OAUTH_REFRESH_TTL  — refresh 秒数，默认 7 天
  GTS_OAUTH_RATE_LIMIT   — 同意页 POST 每 IP 窗口内最大次数，默认 10（0=关闭）
  GTS_OAUTH_RATE_WINDOW  — 限流窗口秒数，默认 300
  GTS_OAUTH_CAPTCHA      — 同意页算术验证码，默认 true
  GTS_OAUTH_PENDING_TTL  — 未完成授权的 client 保留秒数，默认 600；超时主动清理
  GTS_OAUTH_PENDING_MAX  — 未完成授权（注册页进行中）最大同时数，默认 10000

端点（规范路径 + README 回退别名）：
  GET  /.well-known/oauth-protected-resource
  GET  /.well-known/oauth-authorization-server
  POST /oauth/register  | /register
  GET/POST /oauth/authorize | /authorize
  POST /oauth/token     | /token

同意页收集开放平台 AK/SK，校验 loginV2 后签发 access（1h）/ refresh（7d）。
客户端携带的 MCP access JWT 由 http_compat 解出 AK/SK，再走现有 loginV2 调业务。
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import jwt
from cryptography.fernet import Fernet, InvalidToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from authorization import AUTHORIZATION_URL, _login

ACCESS_TTL_SEC = int(os.getenv("GTS_OAUTH_ACCESS_TTL", "3600"))
REFRESH_TTL_SEC = int(os.getenv("GTS_OAUTH_REFRESH_TTL", str(7 * 24 * 3600)))
CODE_TTL_SEC = 600
CAPTCHA_TTL_SEC = 600
JWT_ALG = "HS256"
JWT_ISS_CLAIM = "gts_mcp"

_CONSENT_TEMPLATE_PATH = Path(__file__).resolve().parent / "oauth_consent.html"
_consent_template_cache: Optional[str] = None

# captcha_id -> {answer, exp}
_captchas: Dict[str, Dict[str, Any]] = {}
# rate key -> timestamps
_rate_hits: Dict[str, Deque[float]] = defaultdict(deque)


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def captcha_enabled() -> bool:
    return _env_flag("GTS_OAUTH_CAPTCHA", "true")


def _rate_limit_max() -> int:
    try:
        return max(0, int(os.getenv("GTS_OAUTH_RATE_LIMIT", "10")))
    except ValueError:
        return 10


def _rate_window_sec() -> int:
    try:
        return max(1, int(os.getenv("GTS_OAUTH_RATE_WINDOW", "300")))
    except ValueError:
        return 300


def _client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _rate_allow(bucket: str, *, cost: int = 1) -> bool:
    """滑动窗口限流；返回是否允许。max=0 表示关闭。"""
    limit = _rate_limit_max()
    if limit <= 0:
        return True
    now = time.time()
    window = float(_rate_window_sec())
    q = _rate_hits[bucket]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) + cost > limit:
        return False
    for _ in range(cost):
        q.append(now)
    return True


def _prune_captchas(now: Optional[float] = None) -> None:
    t = now if now is not None else time.time()
    dead = [k for k, v in _captchas.items() if float(v.get("exp") or 0) < t]
    for k in dead:
        _captchas.pop(k, None)


def _issue_captcha() -> Tuple[str, str]:
    """返回 (captcha_id, question_text)。"""
    _prune_captchas()
    a = secrets.randbelow(8) + 2
    b = secrets.randbelow(8) + 2
    cid = secrets.token_urlsafe(18)
    _captchas[cid] = {"answer": str(a + b), "exp": time.time() + CAPTCHA_TTL_SEC}
    return cid, f"{a} + {b} = ?"


def _verify_captcha(captcha_id: str, answer: str) -> bool:
    if not captcha_enabled():
        return True
    cid = (captcha_id or "").strip()
    ans = (answer or "").strip()
    if not cid or not ans:
        return False
    entry = _captchas.pop(cid, None)
    if not entry:
        return False
    if float(entry.get("exp") or 0) < time.time():
        return False
    return str(entry.get("answer")) == ans


def _load_consent_template() -> str:
    global _consent_template_cache
    if _consent_template_cache is None:
        _consent_template_cache = _CONSENT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return _consent_template_cache


def _consent_html(
    *,
    error: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "mcp",
    captcha_id: str = "",
    captcha_question: str = "",
) -> str:
    if captcha_enabled():
        if not captcha_id:
            captcha_id, captcha_question = _issue_captcha()
        captcha_block = (
            '<label for="captcha_answer">验证码</label>'
            '<div class="captcha-row">'
            f'<div class="captcha-q" aria-hidden="true">{html.escape(captcha_question)}</div>'
            '<input id="captcha_answer" name="captcha_answer" type="text" inputmode="numeric" '
            'required autocomplete="off" placeholder="计算结果" style="flex:1"/>'
            "</div>"
        )
    else:
        captcha_id = ""
        captcha_block = ""
    err_block = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return (
        _load_consent_template()
        .replace("{{ERROR_BLOCK}}", err_block)
        .replace("{{CLIENT_ID}}", html.escape(client_id, quote=True))
        .replace("{{REDIRECT_URI}}", html.escape(redirect_uri, quote=True))
        .replace("{{STATE}}", html.escape(state, quote=True))
        .replace("{{CODE_CHALLENGE}}", html.escape(code_challenge, quote=True))
        .replace("{{CODE_CHALLENGE_METHOD}}", html.escape(code_challenge_method, quote=True))
        .replace("{{SCOPE}}", html.escape(scope, quote=True))
        .replace("{{CAPTCHA_ID}}", html.escape(captcha_id, quote=True))
        .replace("{{CAPTCHA_BLOCK}}", captcha_block)
    )


def oauth_enabled() -> bool:
    return bool((os.getenv("GTS_JWT_SECRET") or "").strip())


def _is_cluster_or_pod_host(host: str) -> bool:
    """K8s 内网 Host，不能当作对外 issuer。"""
    h = (host or "").split(":")[0].strip().lower()
    if not h:
        return True
    return (
        h.endswith(".svc")
        or h.endswith(".cluster.local")
        or h.endswith(".svc.cluster.local")
        or h == "gangtise-mcp"
    )


def resolve_public_base(request: Optional[Request] = None) -> str:
    """对外 issuer / resource 基址（无尾斜杠）。

    优先用请求 Host（避免 localhost vs 127.0.0.1 不一致）；
    反代场景用 X-Forwarded-*；内网 Host 则回退 GTS_OAUTH_ISSUER。
    """
    if request is not None:
        headers = {k.decode("latin-1").lower() if isinstance(k, bytes) else k.lower(): (
            v.decode("latin-1") if isinstance(v, bytes) else v
        ) for k, v in (request.scope.get("headers") or [])}
        # Starlette also exposes .headers
        try:
            headers = {k.lower(): v for k, v in request.headers.items()}
        except Exception:
            pass
        return resolve_public_base_from_headers(headers, scheme_default=request.url.scheme or "http")
    return resolve_public_base_from_headers({})


def resolve_public_base_from_headers(
    headers: Dict[str, str],
    *,
    scheme_default: str = "http",
) -> str:
    explicit = (os.getenv("GTS_OAUTH_ISSUER") or "").strip().rstrip("/")
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    xf_proto = (h.get("x-forwarded-proto") or "").split(",")[0].strip()
    xf_host = (h.get("x-forwarded-host") or "").split(",")[0].strip()
    xf_prefix = (
        h.get("x-forwarded-prefix") or os.getenv("GTS_OAUTH_PATH_PREFIX") or ""
    ).strip().rstrip("/")
    host = xf_host or (h.get("host") or "").strip()
    proto = xf_proto or scheme_default or "http"

    if host and not _is_cluster_or_pod_host(host):
        path = ""
        if explicit:
            path = (urlparse(explicit).path or "").rstrip("/")
        elif xf_prefix:
            path = xf_prefix
        return f"{proto}://{host}{path}".rstrip("/")

    return explicit or "http://localhost:8000"


def get_issuer(request: Optional[Request] = None) -> str:
    """兼容旧调用；新代码请传 request。"""
    return resolve_public_base(request)


def public_resource_url(request: Optional[Request] = None) -> str:
    """MCP resource 标识，须与客户端配置的 MCP URL 一致（通常带尾 /）。"""
    return resolve_public_base(request).rstrip("/") + "/"


def resource_metadata_url(request: Optional[Request] = None) -> str:
    return resolve_public_base(request).rstrip("/") + "/.well-known/oauth-protected-resource"


def resource_metadata_url_from_headers(headers: Dict[str, str], *, scheme_default: str = "http") -> str:
    return resolve_public_base_from_headers(headers, scheme_default=scheme_default).rstrip("/") + (
        "/.well-known/oauth-protected-resource"
    )


def _jwt_secret() -> bytes:
    """HMAC 密钥：对配置串做 SHA-256，固定 32 字节，避免 PyJWT InsecureKeyLengthWarning。"""
    secret = (os.getenv("GTS_JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("GTS_JWT_SECRET is not set")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _fernet() -> Fernet:
    raw = (os.getenv("GTS_CRED_ENC_KEY") or "").strip()
    if raw:
        return Fernet(raw.encode("utf-8"))
    # 与 JWT 同源派生，保证未单独配置 CRED_ENC_KEY 时行为稳定
    return Fernet(base64.urlsafe_b64encode(_jwt_secret()))


def _encrypt_creds(ak: str, sk: str) -> str:
    payload = json.dumps({"accessKey": ak, "secretKey": sk}, ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(payload).decode("utf-8")


def _decrypt_creds(token: str) -> Optional[Tuple[str, str]]:
    try:
        raw = _fernet().decrypt(token.encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
        ak = str(data.get("accessKey") or "").strip()
        sk = str(data.get("secretKey") or "").strip()
        if ak and sk:
            return ak, sk
    except (InvalidToken, ValueError, json.JSONDecodeError, TypeError):
        return None
    return None


def _decode_mcp_jwt(token: str) -> Dict[str, Any]:
    """解码本服务签发的 JWT；必须传 audience，否则 PyJWT 对含 aud 的 token 直接 InvalidAudienceError。"""
    return jwt.decode(
        token,
        _jwt_secret(),
        algorithms=[JWT_ALG],
        audience=JWT_ISS_CLAIM,
        options={"require": ["exp", "iat", "typ"]},
    )


def try_mcp_oauth_to_credentials(authorization: str) -> Optional[Tuple[str, str]]:
    """若 Authorization 为本服务签发的 access JWT，返回 (ak, sk)；否则 None。"""
    claims = _try_decode_access_claims(authorization)
    if not claims:
        return None
    cred = claims.get("cred")
    if not isinstance(cred, str):
        return None
    return _decrypt_creds(cred)


def try_mcp_oauth_identity(authorization: str) -> Optional[Dict[str, str]]:
    """从本服务 access JWT 取出 uid/tenantid/productcode（若有）。"""
    claims = _try_decode_access_claims(authorization)
    if not claims:
        return None
    out: Dict[str, str] = {}
    for key in ("uid", "tenantid", "productcode"):
        val = claims.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()
    return out or None


def _try_decode_access_claims(authorization: str) -> Optional[Dict[str, Any]]:
    if not oauth_enabled():
        return None
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or token.count(".") != 2:
        return None
    try:
        claims = _decode_mcp_jwt(token)
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "access":
        return None
    return claims


# --- in-memory stores (单副本部署足够；多副本需外置) ---
# client: status=pending|completed；completed 永久保留，pending 超时清理且有上限
_clients: Dict[str, Dict[str, Any]] = {}
_auth_codes: Dict[str, Dict[str, Any]] = {}


def _pending_ttl_sec() -> int:
    try:
        return max(1, int(os.getenv("GTS_OAUTH_PENDING_TTL", "600")))
    except ValueError:
        return 600


def _pending_max() -> int:
    try:
        return max(1, int(os.getenv("GTS_OAUTH_PENDING_MAX", "10000")))
    except ValueError:
        return 10000


def _prune_pending_clients(now: Optional[float] = None) -> int:
    """清理超时未完成授权的 client；顺带丢掉过期 auth code。返回清理的 client 数。"""
    t = now if now is not None else time.time()
    ttl = float(_pending_ttl_sec())
    dead = [
        cid
        for cid, c in _clients.items()
        if c.get("status", "pending") != "completed"
        and t - float(c.get("touch_at") or c.get("created_at") or 0) > ttl
    ]
    for cid in dead:
        _clients.pop(cid, None)
    expired_codes = [c for c, e in _auth_codes.items() if float(e.get("exp") or 0) < t]
    for c in expired_codes:
        _auth_codes.pop(c, None)
    return len(dead)


def _pending_client_count() -> int:
    return sum(1 for c in _clients.values() if c.get("status", "pending") != "completed")


def _touch_client(client_id: str) -> None:
    entry = _clients.get(client_id)
    if entry and entry.get("status") != "completed":
        entry["touch_at"] = time.time()


def _mark_client_completed(client_id: str) -> None:
    """同意页 AK/SK 成功并签发 authorization code 后标记完成，永久保留。"""
    if not client_id:
        return
    entry = _clients.get(client_id)
    if not entry:
        return
    entry["status"] = "completed"
    entry["completed_at"] = time.time()
    entry["touch_at"] = entry["completed_at"]


def _put_pending_client(
    *,
    client_id: str,
    redirect_uris: List[str],
    client_name: str = "mcp-client",
) -> Optional[str]:
    """登记或刷新 pending client。超额返回 None（调用方应拒绝）。已 completed 的只更新 redirect。"""
    _prune_pending_clients()
    now = time.time()
    existing = _clients.get(client_id)
    if existing and existing.get("status") == "completed":
        uris = existing.setdefault("redirect_uris", [])
        for u in redirect_uris:
            if u and u not in uris:
                uris.append(u)
        return client_id

    if existing:
        # 已有 pending：刷新活跃时间，必要时补 redirect
        existing["touch_at"] = now
        uris = existing.setdefault("redirect_uris", [])
        for u in redirect_uris:
            if u and u not in uris:
                uris.append(u)
        if client_name:
            existing["client_name"] = client_name
        return client_id

    if _pending_client_count() >= _pending_max():
        return None

    _clients[client_id] = {
        "client_id": client_id,
        "redirect_uris": list(redirect_uris),
        "client_name": client_name or "mcp-client",
        "token_endpoint_auth_method": "none",
        "status": "pending",
        "created_at": now,
        "touch_at": now,
    }
    return client_id


def _client_capacity_error(*, as_html: bool = False) -> Response:
    desc = (
        f"pending OAuth registrations full (max {_pending_max()}); "
        f"incomplete entries expire after {_pending_ttl_sec()}s"
    )
    if as_html:
        return HTMLResponse(
            _consent_html(error="授权通道繁忙，请稍后再试（未完成注册会在超时后自动释放）"),
            status_code=503,
        )
    return JSONResponse(
        {"error": "temporarily_unavailable", "error_description": desc},
        status_code=503,
    )


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_redirect_uri(uri: str) -> bool:
    """允许的回调：

    - WorkBuddy / 规范本机：http://127.0.0.1|localhost:{port}/oauth/callback
    - Cursor 桌面：http://127.0.0.1|localhost:{port}/callback（常见 8787）
    - Cursor 自定义协议 / Web Agents（DCR 常一并提交）
    """
    try:
        p = urlparse(uri)
    except Exception:
        return False
    host = (p.hostname or "").lower()
    path = (p.path or "").rstrip("/") or "/"

    # Cursor / 部分客户端在 DCR 时会带多条 redirect_uris，需全部通过校验
    if p.scheme == "cursor" and host in ("anysphere.cursor-mcp",) and path == "/oauth/callback":
        return True
    if (
        p.scheme == "https"
        and host in ("www.cursor.com", "cursor.com")
        and path == "/agents/mcp/oauth/callback"
    ):
        return True

    if p.scheme != "http" or host not in ("127.0.0.1", "localhost"):
        return False
    if path not in ("/oauth/callback", "/callback"):
        return False
    # 本机回调应带端口（Cursor 固定 8787；WorkBuddy 动态端口）
    if p.port is None:
        return False
    return True


def _redirect_uri_error_description() -> str:
    return (
        "redirect_uri must be loopback http://127.0.0.1|localhost:{port}/oauth/callback|/callback, "
        "or cursor://anysphere.cursor-mcp/oauth/callback, "
        "or https://www.cursor.com/agents/mcp/oauth/callback"
    )


def _disabled() -> JSONResponse:
    return JSONResponse(
        {"error": "temporarily_unavailable", "error_description": "OAuth is not configured (set GTS_JWT_SECRET)"},
        status_code=503,
    )


async def oauth_protected_resource(request: Request) -> Response:
    if not oauth_enabled():
        return _disabled()
    issuer = resolve_public_base(request)
    resource = public_resource_url(request)
    return JSONResponse(
        {
            "resource": resource,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"],
        }
    )


async def oauth_authorization_server(request: Request) -> Response:
    if not oauth_enabled():
        return _disabled()
    issuer = resolve_public_base(request)
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp"],
        }
    )


async def register_client(request: Request) -> Response:
    if not oauth_enabled():
        return _disabled()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "redirect_uris required"},
            status_code=400,
        )
    for uri in redirect_uris:
        if not isinstance(uri, str) or not _validate_redirect_uri(uri):
            return JSONResponse(
                {
                    "error": "invalid_redirect_uri",
                    "error_description": _redirect_uri_error_description(),
                },
                status_code=400,
            )
    client_id = str(uuid.uuid4())
    if _put_pending_client(
        client_id=client_id,
        redirect_uris=list(redirect_uris),
        client_name=str(body.get("client_name") or "mcp-client"),
    ) is None:
        return _client_capacity_error(as_html=False)
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )





async def authorize(request: Request) -> Response:
    if not oauth_enabled():
        return _disabled()

    if request.method == "GET":
        q = request.query_params
        client_id = q.get("client_id") or ""
        redirect_uri = q.get("redirect_uri") or ""
        state = q.get("state") or ""
        code_challenge = q.get("code_challenge") or ""
        method = q.get("code_challenge_method") or "S256"
        scope = q.get("scope") or "mcp"
        response_type = q.get("response_type") or "code"

        err = ""
        if response_type != "code":
            err = "仅支持 response_type=code"
        elif not client_id:
            err = "缺少 client_id"
        elif client_id not in _clients:
            # 允许未注册 client（部分客户端跳过 DCR）；仍校验 redirect
            if (
                _put_pending_client(
                    client_id=client_id,
                    redirect_uris=[redirect_uri] if redirect_uri else [],
                    client_name="dynamic",
                )
                is None
            ):
                return _client_capacity_error(as_html=True)
        else:
            _prune_pending_clients()
            if client_id not in _clients:
                # 刚被超时清理
                if (
                    _put_pending_client(
                        client_id=client_id,
                        redirect_uris=[redirect_uri] if redirect_uri else [],
                        client_name="dynamic",
                    )
                    is None
                ):
                    return _client_capacity_error(as_html=True)
            else:
                _touch_client(client_id)
        if not err and not _validate_redirect_uri(redirect_uri):
            err = "redirect_uri 不符合白名单（本机 /oauth/callback|/callback，或 Cursor 回调）"
        if not err and method.upper() != "S256":
            err = "仅支持 code_challenge_method=S256"
        if not err and not code_challenge:
            err = "缺少 code_challenge（PKCE）"

        if err and not _validate_redirect_uri(redirect_uri):
            return HTMLResponse(_consent_html(error=err), status_code=400)

        return HTMLResponse(
            _consent_html(
                error=err,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=method,
                scope=scope,
            )
        )

    # POST
    ip = _client_ip(request)
    rate_key = f"authorize:{ip}"
    if not _rate_allow(rate_key):
        return HTMLResponse(
            _consent_html(error=f"尝试过于频繁，请 {_rate_window_sec()} 秒后再试"),
            status_code=429,
        )

    form = await request.form()
    client_id = str(form.get("client_id") or "")
    redirect_uri = str(form.get("redirect_uri") or "")
    state = str(form.get("state") or "")
    code_challenge = str(form.get("code_challenge") or "")
    method = str(form.get("code_challenge_method") or "S256")
    scope = str(form.get("scope") or "mcp")
    ak = str(form.get("access_key") or "").strip()
    sk = str(form.get("secret_key") or "").strip()
    captcha_id = str(form.get("captcha_id") or "")
    captcha_answer = str(form.get("captcha_answer") or "")

    def _redisplay(msg: str, *, status: int = 400) -> HTMLResponse:
        return HTMLResponse(
            _consent_html(
                error=msg,
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=method,
                scope=scope,
            ),
            status_code=status,
        )

    if not _validate_redirect_uri(redirect_uri):
        return _redisplay("非法 redirect_uri")
    if captcha_enabled() and not _verify_captcha(captcha_id, captcha_answer):
        return _redisplay("验证码错误或已过期，请重试")
    if not ak or not sk:
        return _redisplay("请填写 Access Key 与 Secret Key")

    token, uid, tenantid, productcode = _login(ak, sk)
    if not token:
        return _redisplay(f"鉴权失败，请检查 AK/SK（loginV2: {AUTHORIZATION_URL}）")

    if client_id and client_id not in _clients:
        if (
            _put_pending_client(
                client_id=client_id,
                redirect_uris=[redirect_uri],
                client_name="dynamic",
            )
            is None
        ):
            return _client_capacity_error(as_html=True)
    elif client_id in _clients:
        uris = _clients[client_id].setdefault("redirect_uris", [])
        if redirect_uri not in uris:
            uris.append(redirect_uri)

    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": method.upper(),
        "scope": scope,
        "cred": _encrypt_creds(ak, sk),
        "uid": uid,
        "tenantid": tenantid,
        "productcode": productcode,
        "exp": time.time() + CODE_TTL_SEC,
    }
    _mark_client_completed(client_id)
    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


def _issue_tokens(
    *,
    client_id: str,
    cred: str,
    uid: Optional[str],
    tenantid: Optional[str],
    productcode: Optional[str],
    scope: str,
    issuer: Optional[str] = None,
) -> Dict[str, Any]:
    now = int(time.time())
    iss = (issuer or resolve_public_base()).rstrip("/")
    access = jwt.encode(
        {
            "iss": iss,
            "aud": JWT_ISS_CLAIM,
            "sub": uid or client_id,
            "typ": "access",
            "cid": client_id,
            "cred": cred,
            "uid": uid,
            "tenantid": tenantid,
            "productcode": productcode,
            "scope": scope,
            "iat": now,
            "exp": now + ACCESS_TTL_SEC,
        },
        _jwt_secret(),
        algorithm=JWT_ALG,
    )
    refresh = jwt.encode(
        {
            "iss": iss,
            "aud": JWT_ISS_CLAIM,
            "sub": uid or client_id,
            "typ": "refresh",
            "cid": client_id,
            "cred": cred,
            "uid": uid,
            "tenantid": tenantid,
            "productcode": productcode,
            "scope": scope,
            "iat": now,
            "exp": now + REFRESH_TTL_SEC,
        },
        _jwt_secret(),
        algorithm=JWT_ALG,
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SEC,
        "refresh_token": refresh,
        "scope": scope,
    }


async def token(request: Request) -> Response:
    if not oauth_enabled():
        return _disabled()

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
    else:
        form = await request.form()
        data = {k: str(v) for k, v in form.items()}

    grant = (data.get("grant_type") or "").strip()
    client_id = (data.get("client_id") or "").strip()

    if grant == "authorization_code":
        code = (data.get("code") or "").strip()
        redirect_uri = (data.get("redirect_uri") or "").strip()
        verifier = (data.get("code_verifier") or "").strip()
        entry = _auth_codes.pop(code, None)
        if not entry or entry["exp"] < time.time():
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "code invalid or expired"},
                status_code=400,
            )
        if entry["client_id"] and client_id and entry["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant", "error_description": "client_id mismatch"}, status_code=400)
        if entry["redirect_uri"] != redirect_uri:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
                status_code=400,
            )
        if entry["code_challenge_method"] != "S256":
            return JSONResponse({"error": "invalid_grant", "error_description": "unsupported PKCE"}, status_code=400)
        if _pkce_s256(verifier) != entry["code_challenge"]:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "code_verifier mismatch"},
                status_code=400,
            )
        return JSONResponse(
            _issue_tokens(
                client_id=entry["client_id"] or client_id,
                cred=entry["cred"],
                uid=entry.get("uid"),
                tenantid=entry.get("tenantid"),
                productcode=entry.get("productcode"),
                scope=entry.get("scope") or "mcp",
                issuer=resolve_public_base(request),
            )
        )

    if grant == "refresh_token":
        refresh = (data.get("refresh_token") or "").strip()
        try:
            claims = _decode_mcp_jwt(refresh)
        except jwt.PyJWTError:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "refresh_token invalid or expired"},
                status_code=400,
            )
        if claims.get("typ") != "refresh":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        cred = claims.get("cred")
        if not isinstance(cred, str) or not _decrypt_creds(cred):
            return JSONResponse({"error": "invalid_grant", "error_description": "credentials lost"}, status_code=400)
        # 可选：refresh 时再校验 loginV2
        pair = _decrypt_creds(cred)
        assert pair is not None
        biz, uid, tenantid, productcode = _login(pair[0], pair[1])
        if not biz:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "credentials rejected by loginV2"},
                status_code=400,
            )
        return JSONResponse(
            _issue_tokens(
                client_id=str(claims.get("cid") or client_id),
                cred=cred,
                uid=uid or claims.get("uid"),
                tenantid=tenantid or claims.get("tenantid"),
                productcode=productcode or claims.get("productcode"),
                scope=str(claims.get("scope") or "mcp"),
                issuer=resolve_public_base(request),
            )
        )

    return JSONResponse(
        {"error": "unsupported_grant_type", "error_description": "use authorization_code or refresh_token"},
        status_code=400,
    )


async def health(request: Request) -> Response:
    return JSONResponse(
        {
            "status": "ok",
            "layout": "unified",
            "oauth": oauth_enabled(),
            "issuer": resolve_public_base(request) if oauth_enabled() else None,
        }
    )


def oauth_routes() -> List[Route]:
    """挂载 OAuth + health。规范路径优先，并保留 /authorize|/token|/register 别名。"""
    return [
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", endpoint=oauth_protected_resource, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", endpoint=oauth_authorization_server, methods=["GET"]),
        Route("/oauth/register", endpoint=register_client, methods=["POST"]),
        Route("/register", endpoint=register_client, methods=["POST"]),
        Route("/oauth/authorize", endpoint=authorize, methods=["GET", "POST"]),
        Route("/authorize", endpoint=authorize, methods=["GET", "POST"]),
        Route("/oauth/token", endpoint=token, methods=["POST"]),
        Route("/token", endpoint=token, methods=["POST"]),
    ]

