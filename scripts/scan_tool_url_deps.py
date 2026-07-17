#!/usr/bin/env python3
"""扫描 mcp 包工具脚本，生成 tool → 依赖 _URL 常量映射。

用法：
  python3 scripts/scan_tool_url_deps.py --mcp-root mcp --out tool_url_deps.json

扫描规则：
  - 路径：{mcp_root}/{pkg}/src/{pkg}/*.py
  - 收集 from .utils import 中的 *_URL
  - 对 from .{sibling} 做同级闭包，合并 sibling 的 utils URL
  - 仅输出 tools_registry.TOOL_HANDLERS 中的工具名
  - 忽略 SKILL_CHECK_URL 等基础设施常量
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

DOMAIN_PKGS = (
    "gangtise_agent",
    "gangtise_data",
    "gangtise_file",
    "gangtise_kb",
    "gangtise_private",
)

SKIP_MODULES = frozenset(
    {
        "__init__",
        "tools_registry",
        "utils",
        "authorization",
        "cli_common",
    }
)

IGNORE_URLS = frozenset(
    {
        "SKILL_CHECK_URL",
    }
)


def _is_url_const(name: str) -> bool:
    return name.endswith("_URL") and name not in IGNORE_URLS


def _parse_file(path: Path) -> Optional[ast.AST]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _import_names_from_utils(node: ast.ImportFrom) -> Set[str]:
    if node.level != 1 or node.module != "utils":
        return set()
    out: Set[str] = set()
    for alias in node.names:
        if alias.name == "*":
            continue
        if _is_url_const(alias.name):
            out.add(alias.name)
    return out


def _sibling_modules(node: ast.ImportFrom) -> Set[str]:
    """from .foo / from .foo.bar（仅取第一段）同级模块名。"""
    if node.level != 1:
        return set()
    if not node.module:
        # from . import foo
        return {a.name.split(".", 1)[0] for a in node.names if a.name != "*"}
    if node.module == "utils":
        return set()
    return {node.module.split(".", 1)[0]}


def analyze_module(path: Path) -> Tuple[Set[str], Set[str]]:
    """返回 (直接 utils URL 常量, 同级 sibling 模块名)。"""
    tree = _parse_file(path)
    if tree is None:
        return set(), set()
    urls: Set[str] = set()
    siblings: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        urls |= _import_names_from_utils(node)
        siblings |= _sibling_modules(node)
    siblings -= SKIP_MODULES
    return urls, siblings


def parse_tool_names(registry_path: Path) -> Dict[str, str]:
    """解析 TOOL_HANDLERS：tool_name → 实现模块名（通常同名）。"""
    tree = _parse_file(registry_path)
    if tree is None:
        return {}
    # from .quote import quote_data as quote  → 绑定名 quote 的模块为 quote
    binding_to_mod: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        mod = node.module.split(".", 1)[0]
        for alias in node.names:
            binding_to_mod[alias.asname or alias.name] = mod

    tools: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            if not isinstance(node, ast.Assign):
                continue
            targets = node.targets
            value = node.value
        else:
            targets = [node.target]
            value = node.value
        if not any(isinstance(t, ast.Name) and t.id == "TOOL_HANDLERS" for t in targets):
            continue
        if not isinstance(value, ast.Dict):
            continue
        for k, v in zip(value.keys, value.values):
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                continue
            tool = k.value
            if isinstance(v, ast.Name):
                tools[tool] = binding_to_mod.get(v.id, v.id)
            else:
                tools[tool] = tool
    return tools


def resolve_urls(
    start_mod: str,
    mod_urls: Dict[str, Set[str]],
    mod_siblings: Dict[str, Set[str]],
) -> List[str]:
    seen: Set[str] = set()
    urls: Set[str] = set()
    q: deque[str] = deque([start_mod])
    while q:
        cur = q.popleft()
        if cur in seen or cur in SKIP_MODULES:
            continue
        seen.add(cur)
        urls |= mod_urls.get(cur, set())
        for sib in mod_siblings.get(cur, set()):
            if sib not in seen:
                q.append(sib)
    return sorted(urls)


def scan_package(pkg_dir: Path, pkg_name: str) -> Dict[str, List[str]]:
    src = pkg_dir / "src" / pkg_name
    if not src.is_dir():
        return {}
    registry = src / "tools_registry.py"
    tool_to_mod = parse_tool_names(registry) if registry.is_file() else {}
    if not tool_to_mod:
        # 回退：同名 py 文件（排除 skip）
        for py in src.glob("*.py"):
            stem = py.stem
            if stem not in SKIP_MODULES:
                tool_to_mod[stem] = stem

    mod_urls: Dict[str, Set[str]] = {}
    mod_siblings: Dict[str, Set[str]] = {}
    for py in src.glob("*.py"):
        stem = py.stem
        if stem == "utils":
            continue
        urls, siblings = analyze_module(py)
        # sibling 必须真实存在
        siblings = {s for s in siblings if (src / f"{s}.py").is_file()}
        mod_urls[stem] = urls
        mod_siblings[stem] = siblings

    out: Dict[str, List[str]] = {}
    for tool, mod in sorted(tool_to_mod.items()):
        out[tool] = resolve_urls(mod, mod_urls, mod_siblings)
    return out


def scan_mcp_root(mcp_root: Path, packages: Iterable[str] = DOMAIN_PKGS) -> dict:
    tools: Dict[str, List[str]] = {}
    by_package: Dict[str, Dict[str, List[str]]] = {}
    for pkg in packages:
        pkg_dir = mcp_root / pkg
        if not pkg_dir.is_dir():
            continue
        pkg_tools = scan_package(pkg_dir, pkg)
        by_package[pkg] = pkg_tools
        for name, urls in pkg_tools.items():
            if name in tools and tools[name] != urls:
                # 冲突时合并
                tools[name] = sorted(set(tools[name]) | set(urls))
            else:
                tools[name] = urls
    all_urls = sorted({u for urls in tools.values() for u in urls})
    return {
        "version": 1,
        "mcp_root": str(mcp_root),
        "tools": tools,
        "by_package": by_package,
        "all_urls": all_urls,
        "ignore_urls": sorted(IGNORE_URLS),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="扫描 tool → _URL 依赖并写出 JSON")
    parser.add_argument(
        "--mcp-root",
        type=Path,
        default=Path("mcp"),
        help="mcp 包根目录（含 gangtise_data 等）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tool_url_deps.json"),
        help="输出 JSON 路径",
    )
    args = parser.parse_args(argv)
    mcp_root = args.mcp_root.resolve()
    if not mcp_root.is_dir():
        print(f"错误：mcp-root 不存在: {mcp_root}", file=sys.stderr)
        return 1
    data = scan_mcp_root(mcp_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"已写入 {args.out}：{len(data['tools'])} 个工具，"
        f"{len(data['all_urls'])} 个 URL 常量",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
