"""MCP 工具名到可调用实现的注册表。"""
from __future__ import annotations

from typing import Any, Callable, Dict

from .block_constituents import block_constituents_data as block_constituents
from .company_indicator import company_indicator_data as company_indicator
from .concept import concept_data as concept
from .earning_forecast import earning_forecast_data as earning_forecast
from .financial import financial_data as financial
from .fund_flow import fund_flow_data as fund_flow
from .industry_indicator import industry_indicator_data as industry_indicator
from .main_business import main_business_data as main_business
from .quote import quote_data as quote
from .security import security_search as security
from .shareholder import shareholder_data as shareholder
from .valuation import valuation_data as valuation


ToolHandler = Callable[..., Any]


TOOL_HANDLERS: Dict[str, ToolHandler] = {
    "block_constituents": block_constituents,
    "company_indicator": company_indicator,
    "concept": concept,
    "earning_forecast": earning_forecast,
    "financial": financial,
    "fund_flow": fund_flow,
    "industry_indicator": industry_indicator,
    "main_business": main_business,
    "quote": quote,
    "security": security,
    "shareholder": shareholder,
    "valuation": valuation,
}

# CLI / 脚本专用；MCP API schema 与 callTool 均不暴露
CLI_ONLY_PARAMS = frozenset({"output_dir"})

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
) | CLI_ONLY_PARAMS
