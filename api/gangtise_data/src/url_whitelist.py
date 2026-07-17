"""按工具 URL 依赖白名单过滤 MCP 工具可见性。

依赖图由 scripts/scan_tool_url_deps.py 在构建期生成（tool_url_deps.json）。
运行时：
  - get_white_list()：用户可访问的 URL 常量集合（当前 stub 返回全量）
  - 若 tool 无 URL 依赖 → 放行
  - 若 tool 有 URL 依赖且任一不在白名单（含白名单为空 / 用户被 ban）→ 屏蔽

环境变量：
  TOOL_URL_DEPS_PATH  依赖 JSON 路径（默认 $GTS_MCP_ROOT/tool_url_deps.json）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_DEPS_CACHE: Optional[Dict[str, Any]] = None


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
        data = {"version": 1, "tools": {}, "all_urls": [], "by_package": {}}
        print(
            "[url_whitelist] 未找到 tool_url_deps.json，依赖图为空"
            "（无 URL 依赖的工具仍可放行）",
            file=sys.stderr,
        )
    _DEPS_CACHE = data
    return data


def tool_url_deps(tool_name: str) -> List[str]:
    tools = load_tool_url_deps().get("tools") or {}
    return list(tools.get(tool_name) or [])


def all_known_urls() -> Set[str]:
    data = load_tool_url_deps()
    urls = data.get("all_urls")
    if isinstance(urls, list) and urls:
        return set(urls)
    tools = data.get("tools") or {}
    out: Set[str] = set()
    for u in tools.values():
        if isinstance(u, list):
            out.update(u)
    return out


def get_white_list() -> Set[str]:
    """返回当前用户可访问的 URL 常量集合。

    会先解析当前 Authorization（直传头 / 环境变量 / AK·SK loginV2），
    供后续权限接口使用；AK/SK 路径下此处会触发换票。

    TODO: 用 authorization 调权限接口；用户被 ban 时返回空集。
    当前 stub：返回依赖图中的全量 URL（等价于不按权限裁剪）。
    """
    authorization: Optional[str] = None
    try:
        from authorization import get_authorization_token

        authorization = get_authorization_token()
    except Exception as e:
        print(f"[url_whitelist] 解析 Authorization 失败: {e}", file=sys.stderr)
    return _white_list_for_authorization(authorization)


def _white_list_for_authorization(authorization: Optional[str]) -> Set[str]:
    """按 Authorization 查询白名单；stub 阶段忽略具体 token。"""
    _ = authorization  # 权限接口就绪后在此发起请求
    return set(all_known_urls())


def tool_denied_reason(
    tool_name: str, whitelist: Optional[Set[str]] = None
) -> Optional[str]:
    """若应屏蔽则返回原因，否则 None。

    - 无 URL 依赖 → 放行
    - 有依赖且白名单缺任一（含白名单为空）→ 屏蔽
    """
    urls = tool_url_deps(tool_name)
    if not urls:
        return None
    wl = get_white_list() if whitelist is None else whitelist
    missing = [u for u in urls if u not in wl]
    if not missing:
        return None
    if not wl:
        return "白名单为空（用户无权限或已被 ban）"
    return f"缺少 URL 权限: {', '.join(missing)}"


def is_tool_allowed(tool_name: str, whitelist: Optional[Set[str]] = None) -> bool:
    return tool_denied_reason(tool_name, whitelist) is None
