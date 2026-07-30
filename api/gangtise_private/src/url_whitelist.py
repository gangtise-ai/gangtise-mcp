"""按工具 URL 依赖白名单 / 部署黑名单过滤 MCP 工具可见性。

依赖图由 scripts/scan_tool_url_deps.py 在构建期生成（tool_url_deps.json，version=2，
值为 API path，如 /broker-report/getList；仅含各工具自身脚本中的 *_URL，无 sibling 连锁）。

运行时：
  - get_tool_blacklist()：部署级工具黑名单（绝对屏蔽 list/call）
  - get_white_list()：调用 open-data ``/api/getList``，按当前 Authorization
    返回用户可访问的 API path 集合
  - 若 tool 在黑名单 → 屏蔽
  - 若 tool 无 path 依赖 → 放行
  - 若 tool 有依赖且任一不在白名单（含白名单为空 / 用户被 ban）→ 屏蔽

环境变量：
  TOOL_URL_DEPS_PATH      依赖 JSON 路径（默认 $GTS_MCP_ROOT/tool_url_deps.json）
  MCP_TOOL_BLACKLIST      逗号分隔工具名黑名单；空=不额外屏蔽
  GANGTISE_DATA_DOMAIN    getList 基址（默认公网 open-data；集群内由 ConfigMap 注入）
  MCP_API_GETLIST_PATH    getList 路径，默认 /api/getList
  MCP_WHITELIST_CACHE_SEC 白名单缓存秒数，默认 300
  MCP_WHITELIST_STRICT    true 时 getList 失败返回空集（默认 false：失败回退全量，避免误伤）
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

_DEPS_CACHE: Optional[Dict[str, Any]] = None

# (auth_key, expires_at, paths)
_WL_CACHE_LOCK = threading.Lock()
_WL_CACHE: Dict[str, Tuple[float, Set[str]]] = {}


def _candidate_deps_paths() -> List[Path]:
    paths: List[Path] = []
    env = os.getenv("TOOL_URL_DEPS_PATH", "").strip()
    if env:
        paths.append(Path(env))
    root = os.getenv("GTS_MCP_ROOT", "").strip()
    if root:
        paths.append(Path(root) / "tool_url_deps.json")
    here = Path(__file__).resolve()
    for parent in here.parents:
        paths.append(parent / "tool_url_deps.json")
        if parent.name == "mcps":
            break
    paths.append(Path("/opt/mcp/tool_url_deps.json"))
    seen: Set[str] = set()
    out: List[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _try_runtime_scan() -> Optional[Dict[str, Any]]:
    """本地未生成 JSON 时，尝试扫描源码目录。"""
    candidates: List[Path] = []
    root = os.getenv("GTS_MCP_ROOT", "").strip()
    if root:
        candidates.append(Path(root) / "mcp")
        candidates.append(Path(root))
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "mcp" / "gangtise_data").is_dir():
            candidates.append(parent / "mcp")
        if parent.name == "mcp" and (parent / "gangtise_data").is_dir():
            candidates.append(parent)
    scripts = None
    for parent in here.parents:
        s = parent / "scripts" / "scan_tool_url_deps.py"
        if s.is_file():
            scripts = s
            break
    if scripts is None:
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("scan_tool_url_deps", scripts)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for mcp_root in candidates:
        if not mcp_root.is_dir() or not (mcp_root / "gangtise_data").is_dir():
            continue
        try:
            return mod.scan_mcp_root(mcp_root)
        except Exception as e:
            print(f"[url_whitelist] 运行时扫描失败 {mcp_root}: {e}", file=sys.stderr)
    return None


def load_tool_url_deps(*, force: bool = False) -> Dict[str, Any]:
    global _DEPS_CACHE
    if _DEPS_CACHE is not None and not force:
        return _DEPS_CACHE
    data: Optional[Dict[str, Any]] = None
    for path in _candidate_deps_paths():
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError) as e:
                print(f"[url_whitelist] 读取依赖图失败 {path}: {e}", file=sys.stderr)
    if data is None:
        data = _try_runtime_scan()
    if data is None:
        data = {"version": 2, "tools": {}, "all_urls": [], "by_package": {}}
        print(
            "[url_whitelist] 未找到 tool_url_deps.json，依赖图为空"
            "（无 URL 依赖的工具仍可放行）",
            file=sys.stderr,
        )
    _DEPS_CACHE = data
    return data


def _looks_like_api_path(s: str) -> bool:
    return bool(s) and s.startswith("/") and not s.endswith("_URL")


def tool_url_deps(tool_name: str) -> List[str]:
    """工具依赖的 API path 列表（version≥2）；旧版常量名会尽量经 url_constants 映射。"""
    data = load_tool_url_deps()
    tools = data.get("tools") or {}
    raw = list(tools.get(tool_name) or [])
    if not raw:
        return []
    if all(_looks_like_api_path(u) for u in raw):
        return raw
    # 兼容 version=1：常量名 → path
    const_map = data.get("url_constants") or {}
    paths: List[str] = []
    for u in raw:
        if _looks_like_api_path(u):
            paths.append(u)
        elif u in const_map and _looks_like_api_path(str(const_map[u])):
            paths.append(str(const_map[u]))
    return sorted(set(paths))


def all_known_urls() -> Set[str]:
    data = load_tool_url_deps()
    urls = data.get("all_urls")
    if isinstance(urls, list) and urls and all(_looks_like_api_path(str(u)) for u in urls):
        return {str(u) for u in urls}
    # 从 tools 聚合 / 常量映射
    out: Set[str] = set()
    const_map = data.get("url_constants") or {}
    tools = data.get("tools") or {}
    for deps in tools.values():
        if not isinstance(deps, list):
            continue
        for u in deps:
            if _looks_like_api_path(str(u)):
                out.add(str(u))
            elif u in const_map:
                out.add(str(const_map[u]))
    if not out and isinstance(const_map, dict):
        out = {str(v) for v in const_map.values() if _looks_like_api_path(str(v))}
    return out


def _normalize_path(p: str) -> str:
    s = (p or "").strip()
    if not s:
        return ""
    if "://" in s:
        s = urlparse(s).path or ""
    if not s.startswith("/"):
        s = "/" + s
    # 去掉偶发的 /application/open-* 前缀
    for marker in (
        "/application/open-data",
        "/application/open-insight",
        "/application/open-quote",
        "/application/open-reference",
        "/application/open-fundamental",
        "/application/open-alternative",
        "/application/open-indicator",
        "/application/open-vault",
    ):
        if s.startswith(marker + "/") or s == marker:
            s = s[len(marker) :] or "/"
            break
    return s.rstrip("/") or "/"


def _path_aliases(p: str) -> Set[str]:
    """路径别名：兼容 management_discuss vs management-discuss。"""
    n = _normalize_path(p)
    if n == "/":
        return {n}
    out = {n, n.replace("_", "-"), n.replace("-", "_")}
    return {x for x in out if x}


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def _whitelist_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("MCP_WHITELIST_CACHE_SEC", "300")))
    except ValueError:
        return 300.0


def _ensure_bearer(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return t
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def _resolve_data_domain() -> str:
    try:
        from authorization import resolve_gangtise_domain

        return resolve_gangtise_domain(
            "GANGTISE_DATA_DOMAIN",
            "https://openapi.gangtise.com/application/open-data",
        ).rstrip("/")
    except Exception:
        raw = (os.getenv("GANGTISE_DATA_DOMAIN") or "").strip().rstrip("/")
        return raw or "https://openapi.gangtise.com/application/open-data"


def _getlist_url() -> str:
    path = (os.getenv("MCP_API_GETLIST_PATH") or "/api/getList").strip() or "/api/getList"
    if not path.startswith("/"):
        path = "/" + path
    return _resolve_data_domain() + path


def _parse_getlist_payload(body: Any) -> Optional[Set[str]]:
    if not isinstance(body, dict):
        return None
    ok = body.get("status")
    if ok is None:
        ok = str(body.get("code", "")) in ("000000", "0")
    if not ok and str(body.get("code", "")) not in ("000000", "0"):
        return None
    data = body.get("data")
    if not isinstance(data, list):
        return None
    out: Set[str] = set()
    for item in data:
        if isinstance(item, str) and item.strip():
            out |= _path_aliases(item)
        elif isinstance(item, dict):
            for key in ("path", "url", "api", "apiPath"):
                v = item.get(key)
                if isinstance(v, str) and v.strip():
                    out |= _path_aliases(v)
                    break
    return out


def _fetch_whitelist_from_getlist(authorization: str) -> Optional[Set[str]]:
    """请求 /api/getList；成功返回 path 集合，失败返回 None。"""
    import requests

    url = _getlist_url()
    headers = {
        "Authorization": _ensure_bearer(authorization),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        # 内网实测为 GET；部分网关亦接受 POST
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code >= 400:
            resp = requests.post(url, headers=headers, json={}, timeout=30)
        body = resp.json()
    except Exception as e:
        print(f"[url_whitelist] getList 请求失败 url={url} err={e}", file=sys.stderr)
        return None
    parsed = _parse_getlist_payload(body)
    if parsed is None:
        print(
            f"[url_whitelist] getList 响应异常 status={getattr(resp, 'status_code', '?')} "
            f"body={str(body)[:300]}",
            file=sys.stderr,
        )
        return None
    return parsed


def get_white_list() -> Set[str]:
    """返回当前用户可访问的 API path 集合（含 _/- 别名）。"""
    authorization: Optional[str] = None
    try:
        from authorization import get_authorization_token

        authorization = get_authorization_token()
    except Exception as e:
        print(f"[url_whitelist] 解析 Authorization 失败: {e}", file=sys.stderr)
    return _white_list_for_authorization(authorization)


def _white_list_for_authorization(authorization: Optional[str]) -> Set[str]:
    """按 Authorization 查询 /api/getList。"""
    if not authorization or not str(authorization).strip():
        # 无鉴权：严格则空，否则全量（本地调试）
        if _env_flag("MCP_WHITELIST_STRICT", "false"):
            return set()
        return set(all_known_urls()) | {
            a for u in all_known_urls() for a in _path_aliases(u)
        }

    auth_key = _ensure_bearer(str(authorization))
    ttl = _whitelist_cache_ttl()
    now = time.time()
    with _WL_CACHE_LOCK:
        cached = _WL_CACHE.get(auth_key)
        if cached is not None and cached[0] > now:
            return set(cached[1])

    fetched = _fetch_whitelist_from_getlist(auth_key)
    if fetched is None:
        if _env_flag("MCP_WHITELIST_STRICT", "false"):
            return set()
        print(
            "[url_whitelist] getList 失败，回退全量 URL（MCP_WHITELIST_STRICT=false）",
            file=sys.stderr,
        )
        fetched = set(all_known_urls()) | {
            a for u in all_known_urls() for a in _path_aliases(u)
        }

    if ttl > 0:
        with _WL_CACHE_LOCK:
            _WL_CACHE[auth_key] = (now + ttl, set(fetched))
    return fetched


def path_in_whitelist(path: str, whitelist: Set[str]) -> bool:
    if not path:
        return True
    return bool(_path_aliases(path) & whitelist)


_BLACKLIST_CACHE: Optional[Set[str]] = None


def _parse_name_list(raw: str) -> Set[str]:
    out: Set[str] = set()
    for part in raw.replace("，", ",").replace(";", ",").replace(" ", ",").split(","):
        name = part.strip()
        if name:
            out.add(name)
    return out


def get_tool_blacklist(*, force: bool = False) -> Set[str]:
    """部署级工具黑名单（环境变量 MCP_TOOL_BLACKLIST）。"""
    global _BLACKLIST_CACHE
    if _BLACKLIST_CACHE is not None and not force:
        return _BLACKLIST_CACHE
    raw = os.getenv("MCP_TOOL_BLACKLIST", "").strip()
    _BLACKLIST_CACHE = _parse_name_list(raw)
    return _BLACKLIST_CACHE


def tool_denied_reason(
    tool_name: str, whitelist: Optional[Set[str]] = None
) -> Optional[str]:
    """若应屏蔽则返回原因，否则 None。"""
    if tool_name in get_tool_blacklist():
        return f"工具已列入部署黑名单: {tool_name}"
    urls = tool_url_deps(tool_name)
    if not urls:
        return None
    wl = get_white_list() if whitelist is None else whitelist
    missing = [u for u in urls if not path_in_whitelist(u, wl)]
    if not missing:
        return None
    if not wl:
        return "白名单为空（用户无权限或已被 ban）"
    return f"缺少 URL 权限: {', '.join(missing)}"


def is_tool_allowed(tool_name: str, whitelist: Optional[Set[str]] = None) -> bool:
    return tool_denied_reason(tool_name, whitelist) is None
