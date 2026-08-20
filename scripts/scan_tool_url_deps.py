#!/usr/bin/env python3
"""扫描 mcp 包工具脚本，生成 tool → 依赖 API path 映射。

用法：
  python3 scripts/scan_tool_url_deps.py --mcp-root mcp --out tool_url_deps.json

扫描规则：
  - 路径：{mcp_root}/{pkg}/src/{pkg}/*.py
  - **仅**统计工具自身脚本中出现的 ``*_URL``（from .utils import / 文件内引用）
  - **不做** sibling 闭包：引用 search_institution 等不会把对方的 _URL 算进来
  - 仅输出 tools_registry.TOOL_HANDLERS 中的工具名
  - 忽略 SKILL_CHECK_URL 等基础设施常量

输出 version=2：tools / all_urls 存 API path（与 open-data /api/getList 对齐）。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

DOMAIN_PKGS = (
    "gangtise_agent",
    "gangtise_data",
    "gangtise_file",
    "gangtise_kb",
    "gangtise_private",
    "gangtise_pdf",
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
    """已废弃：扫描不再做 sibling 闭包，保留以免外部误用报错。"""
    return set()


def extract_url_const_paths(utils_py: Path) -> Dict[str, str]:
    """从 utils.py 解析 *_URL 常量名 → API path（以 / 开头的后缀）。"""
    tree = _parse_file(utils_py)
    if tree is None:
        return {}
    const_path: Dict[str, str] = {}
    alias: Dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if not _is_url_const(name):
            continue
        val = node.value
        if isinstance(val, ast.BinOp) and isinstance(val.op, ast.Add):
            right = val.right
            if (
                isinstance(right, ast.Constant)
                and isinstance(right.value, str)
                and right.value.startswith("/")
            ):
                const_path[name] = right.value
                continue
        if isinstance(val, ast.Name) and _is_url_const(val.id):
            alias[name] = val.id
            continue
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            s = val.value.strip()
            if s.startswith("/"):
                const_path[name] = s
            elif "://" in s:
                # 完整 URL：取 path（去掉 query）
                try:
                    from urllib.parse import urlparse

                    path = urlparse(s).path or ""
                    # 去掉 /application/open-xxx 前缀若存在
                    for marker in (
                        "/application/open-data",
                        "/application/open-insight",
                        "/application/open-quote",
                        "/application/open-reference",
                        "/application/open-fundamental",
                        "/application/open-alternative",
                        "/application/open-indicator",
                        "/application/open-vault",
                        "/application/open-openai",
                        "/application/open-ai",
                    ):
                        if marker in path:
                            path = path.split(marker, 1)[1] or path
                            break
                    if path.startswith("/"):
                        const_path[name] = path
                except Exception:
                    pass

    changed = True
    while changed:
        changed = False
        for a, b in list(alias.items()):
            if a in const_path:
                continue
            if b in const_path:
                const_path[a] = const_path[b]
                changed = True
    return const_path


def analyze_module(path: Path) -> Set[str]:
    """仅返回本文件中出现的 ``*_URL`` 常量名（不含 sibling 依赖）。"""
    tree = _parse_file(path)
    if tree is None:
        return set()
    urls: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            urls |= _import_names_from_utils(node)
        elif isinstance(node, ast.Name) and _is_url_const(node.id):
            urls.add(node.id)
        elif isinstance(node, ast.Attribute) and _is_url_const(node.attr):
            urls.add(node.attr)
    return urls


def parse_tool_names(registry_path: Path) -> Dict[str, str]:
    """解析 TOOL_HANDLERS：tool_name → 实现模块名（通常同名）。"""
    tree = _parse_file(registry_path)
    if tree is None:
        return {}
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


def scan_package(pkg_dir: Path, pkg_name: str) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """返回 (tool → path 列表, 本包 const→path)。"""
    src = pkg_dir / "src" / pkg_name
    if not src.is_dir():
        return {}, {}
    registry = src / "tools_registry.py"
    tool_to_mod = parse_tool_names(registry) if registry.is_file() else {}
    if not tool_to_mod:
        for py in src.glob("*.py"):
            stem = py.stem
            if stem not in SKIP_MODULES:
                tool_to_mod[stem] = stem

    const_to_path = extract_url_const_paths(src / "utils.py") if (src / "utils.py").is_file() else {}

    mod_urls: Dict[str, Set[str]] = {}
    for py in src.glob("*.py"):
        stem = py.stem
        if stem == "utils":
            continue
        mod_urls[stem] = analyze_module(py)

    out: Dict[str, List[str]] = {}
    for tool, mod in sorted(tool_to_mod.items()):
        consts = sorted(mod_urls.get(mod, set()))
        paths: Set[str] = set()
        for c in consts:
            p = const_to_path.get(c)
            if p:
                paths.add(p)
        out[tool] = sorted(paths)
    return out, const_to_path


def scan_mcp_root(mcp_root: Path, packages: Iterable[str] = DOMAIN_PKGS) -> dict:
    tools: Dict[str, List[str]] = {}
    by_package: Dict[str, Dict[str, List[str]]] = {}
    url_constants: Dict[str, str] = {}
    for pkg in packages:
        pkg_dir = mcp_root / pkg
        if not pkg_dir.is_dir():
            continue
        pkg_tools, const_map = scan_package(pkg_dir, pkg)
        by_package[pkg] = pkg_tools
        url_constants.update(const_map)
        for name, paths in pkg_tools.items():
            if name in tools and tools[name] != paths:
                tools[name] = sorted(set(tools[name]) | set(paths))
            else:
                tools[name] = paths
    all_urls = sorted({u for urls in tools.values() for u in urls})
    return {
        "version": 2,
        "mcp_root": str(mcp_root),
        "tools": tools,
        "by_package": by_package,
        "all_urls": all_urls,
        "url_constants": dict(sorted(url_constants.items())),
        "ignore_urls": sorted(IGNORE_URLS),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="扫描 tool → API path 依赖并写出 JSON")
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
        f"{len(data['all_urls'])} 个 API path",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
