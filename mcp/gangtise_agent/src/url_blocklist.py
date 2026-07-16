"""按 URL_BLOCK_LIST 过滤含屏蔽 URL 常量的工具脚本。

环境变量示例：
  URL_BLOCK_LIST=EDB_SEARCH_URL,EDB_GET_DATA_URL

仅检查工具自身脚本（如 industry_indicator.py），不检查 utils.py 定义处。
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

ToolHandler = Callable[..., Any]


def parse_url_block_list(raw: Optional[str] = None) -> Set[str]:
    text = raw if raw is not None else os.getenv("URL_BLOCK_LIST", "")
    return {p.strip() for p in str(text).replace("，", ",").split(",") if p.strip()}


def _handler_script_path(handler: ToolHandler) -> Optional[str]:
    try:
        mod = inspect.getmodule(handler)
    except Exception:
        return None
    if mod is None:
        return None
    path = getattr(mod, "__file__", None)
    if not path or not str(path).endswith(".py"):
        return None
    return str(path)


def handler_blocked_by(handler: ToolHandler, block_list: Set[str]) -> Optional[str]:
    """若脚本源码引用了屏蔽常量名，返回命中的常量名。"""
    if not block_list:
        return None
    path = _handler_script_path(handler)
    if not path:
        return None
    base = os.path.basename(path)
    if base in ("utils.py", "tools_registry.py", "__init__.py", "authorization.py"):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return None
    for name in block_list:
        if re.search(rf"\b{re.escape(name)}\b", src):
            return name
    return None


def filter_blocked_handlers(
    handlers: Dict[str, ToolHandler],
    block_list: Optional[Set[str]] = None,
    *,
    log: bool = True,
) -> Tuple[Dict[str, ToolHandler], List[Tuple[str, str]]]:
    """返回 (保留的 handlers, [(tool_name, blocked_const), ...])。"""
    bl = parse_url_block_list() if block_list is None else block_list
    if not bl:
        return dict(handlers), []
    kept: Dict[str, ToolHandler] = {}
    blocked: List[Tuple[str, str]] = []
    for name, handler in handlers.items():
        hit = handler_blocked_by(handler, bl)
        if hit:
            blocked.append((name, hit))
        else:
            kept[name] = handler
    if log and blocked:
        for name, hit in blocked:
            print(
                f"[URL_BLOCK_LIST] 跳过工具 {name}（脚本引用 {hit}）",
                file=sys.stderr,
            )
    return kept, blocked
