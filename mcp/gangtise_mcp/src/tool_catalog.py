"""从依赖的五域 mcp 包聚合叶子工具目录（无内嵌副本）。"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from references_loader import ToolSpec, load_all_tool_specs
from tools_registry import DOMAIN_TOOL_NAMES, INTERNAL_PARAMS, TOOL_HANDLERS

ToolHandler = Callable[..., Any]

DOMAIN_PACKAGE_ORDER: Tuple[str, ...] = tuple(DOMAIN_TOOL_NAMES.keys())

_LABEL_TO_PKG = {
    "gangtise-agent": "gangtise_agent",
    "gangtise-data": "gangtise_data",
    "gangtise-file": "gangtise_file",
    "gangtise-kb": "gangtise_kb",
    "gangtise-private": "gangtise_private",
}


def _domain_references_dir(label: str) -> Path:
    pkg = _LABEL_TO_PKG[label]
    mod = importlib.import_module(pkg)
    return Path(mod.__file__).resolve().parent / "references"


@dataclass
class Catalog:
    handlers: Dict[str, ToolHandler] = field(default_factory=dict)
    specs: List[ToolSpec] = field(default_factory=list)
    spec_map: Dict[str, ToolSpec] = field(default_factory=dict)
    source_by_tool: Dict[str, str] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


_CATALOG: Optional[Catalog] = None


def load_catalog(*, force: bool = False) -> Catalog:
    global _CATALOG
    if _CATALOG is not None and not force:
        return _CATALOG

    cat = Catalog()
    try:
        if not TOOL_HANDLERS:
            raise RuntimeError(
                "tools_registry.TOOL_HANDLERS 为空；请安装五域 mcp 依赖并确认 sync 已运行"
            )

        all_specs: Dict[str, ToolSpec] = {}
        for label in DOMAIN_TOOL_NAMES:
            refs = _domain_references_dir(label)
            if not refs.is_dir():
                cat.errors[label] = f"缺少 references/: {refs}"
                continue
            for s in load_all_tool_specs(refs):
                all_specs[s.name] = s

        # 全量注册；按用户白名单的裁剪在 list_tools / call_tool 时进行
        for domain_label, names in DOMAIN_TOOL_NAMES.items():
            for name in names:
                handler = TOOL_HANDLERS.get(name)
                if handler is None:
                    cat.errors[name] = f"{domain_label} 声明了 {name} 但无 handler"
                    continue
                if name in cat.handlers:
                    cat.errors[name] = (
                        f"工具名冲突: {name}（{cat.source_by_tool[name]} vs {domain_label}）"
                    )
                    continue
                cat.handlers[name] = handler
                cat.source_by_tool[name] = domain_label
                spec = all_specs.get(name)
                if spec is not None:
                    cat.specs.append(spec)
                    cat.spec_map[name] = spec
                else:
                    cat.errors[name] = f"缺少 references/{name}.yaml"

        order_domain = {d: i for i, d in enumerate(DOMAIN_PACKAGE_ORDER)}

        def _sort_key(spec: ToolSpec) -> Tuple[int, str]:
            src = cat.source_by_tool.get(spec.name, "")
            return (order_domain.get(src, 99), spec.name)

        cat.specs.sort(key=_sort_key)
    except Exception as e:
        cat.errors["catalog"] = str(e)

    _CATALOG = cat
    return cat


DOMAIN_PACKAGES = DOMAIN_PACKAGE_ORDER

# silence unused for re-export compatibility
_ = INTERNAL_PARAMS
