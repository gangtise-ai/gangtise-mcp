"""从依赖的五域 mcp 包加载 handlers / references（无内嵌副本）。"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from domains import DOMAINS, DomainDef
from references_loader import ToolSpec, load_all_tool_specs
from tools_registry import DOMAIN_TOOL_NAMES, INTERNAL_PARAMS, TOOL_HANDLERS

ToolHandler = Callable[..., Any]

# package_dir → importable Python package that owns references/
_DOMAIN_MOD = {
    "gangtise_agent": "gangtise_agent",
    "gangtise_data": "gangtise_data",
    "gangtise_file": "gangtise_file",
    "gangtise_kb": "gangtise_kb",
    "gangtise_private": "gangtise_private",
}


def domain_references_dir(package_dir: str) -> Path:
    mod_name = _DOMAIN_MOD[package_dir]
    mod = importlib.import_module(mod_name)
    return Path(mod.__file__).resolve().parent / "references"


@dataclass
class DomainRuntime:
    domain: DomainDef
    handlers: Dict[str, ToolHandler]
    internal_params: frozenset
    specs: List[ToolSpec]
    spec_map: Dict[str, ToolSpec]


_CACHE: Dict[str, DomainRuntime] = {}
_LOAD_ERRORS: Dict[str, str] = {}


def load_domain(domain: DomainDef, *, force: bool = False) -> DomainRuntime:
    if not force and domain.tool_name in _CACHE:
        return _CACHE[domain.tool_name]

    names = DOMAIN_TOOL_NAMES.get(domain.tool_name)
    if not names:
        raise KeyError(f"未知域入口或未安装域包: {domain.tool_name}")

    missing = [n for n in names if n not in TOOL_HANDLERS]
    if missing:
        raise KeyError(f"{domain.tool_name} 缺少 handler: {', '.join(missing)}")

    handlers = {n: TOOL_HANDLERS[n] for n in names}
    # 全量加载；叶子工具按用户白名单裁剪在 list/call 路径进行
    refs = domain_references_dir(domain.package_dir)
    if not refs.is_dir():
        raise FileNotFoundError(f"缺少 references/: {refs}")
    specs = [s for s in load_all_tool_specs(refs) if s.name in handlers]
    order = {n: i for i, n in enumerate(names)}
    specs.sort(key=lambda s: order.get(s.name, 10_000))
    spec_map = {s.name: s for s in specs}

    runtime = DomainRuntime(
        domain=domain,
        handlers=handlers,
        internal_params=INTERNAL_PARAMS,
        specs=specs,
        spec_map=spec_map,
    )
    _CACHE[domain.tool_name] = runtime
    _LOAD_ERRORS.pop(domain.tool_name, None)
    return runtime


def try_load_domain(domain: DomainDef) -> Optional[DomainRuntime]:
    try:
        return load_domain(domain)
    except Exception as e:
        _LOAD_ERRORS[domain.tool_name] = str(e)
        return None


def load_error(tool_name: str) -> Optional[str]:
    return _LOAD_ERRORS.get(tool_name)


def preload_all() -> Dict[str, DomainRuntime]:
    out: Dict[str, DomainRuntime] = {}
    for d in DOMAINS:
        rt = try_load_domain(d)
        if rt is not None:
            out[d.tool_name] = rt
    return out
