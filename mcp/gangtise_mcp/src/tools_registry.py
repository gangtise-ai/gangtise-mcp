"""合并五域叶子工具（运行时从各域 mcp 包导入，无内嵌副本）。"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from gangtise_agent import tools_registry as _agent_reg
from gangtise_data import tools_registry as _data_reg
from gangtise_file import tools_registry as _file_reg
from gangtise_kb import tools_registry as _kb_reg
from gangtise_private import tools_registry as _private_reg
from gangtise_screener import tools_registry as _screener_reg
from gangtise_pdf import tools_registry as _pdf_reg

ToolHandler = Callable[..., Any]

TOOL_HANDLERS: Dict[str, ToolHandler] = {}
DOMAIN_TOOL_NAMES: Dict[str, List[str]] = {}

DOMAIN_TOOL_NAMES["gangtise-agent"] = list(_agent_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_agent_reg.TOOL_HANDLERS)

DOMAIN_TOOL_NAMES["gangtise-data"] = list(_data_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_data_reg.TOOL_HANDLERS)

DOMAIN_TOOL_NAMES["gangtise-file"] = list(_file_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_file_reg.TOOL_HANDLERS)

DOMAIN_TOOL_NAMES["gangtise-kb"] = list(_kb_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_kb_reg.TOOL_HANDLERS)

DOMAIN_TOOL_NAMES["gangtise-private"] = list(_private_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_private_reg.TOOL_HANDLERS)

DOMAIN_TOOL_NAMES["gangtise-screener"] = list(_screener_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_screener_reg.TOOL_HANDLERS)

DOMAIN_TOOL_NAMES["gangtise-pdf"] = list(_pdf_reg.TOOL_HANDLERS)
TOOL_HANDLERS.update(_pdf_reg.TOOL_HANDLERS)

# 勿把 output_dir 全局列入 INTERNAL：screener YAML 会暴露该参数，
# 列入后 call_tool 会误报「未知参数: output_dir」。data 域 YAML 通常不暴露，
# 客户端不传即可；若传入则 handler 本身支持。
INTERNAL_PARAMS = frozenset(
    {
        "headers",
        "authorization",
        "append_file_hint",
        "meta",
        "meta_by_id",
        "indicator_meta",
        "kwargs",
    }
)
