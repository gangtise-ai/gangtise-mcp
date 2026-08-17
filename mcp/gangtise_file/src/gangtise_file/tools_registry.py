"""MCP 工具名到可调用实现的注册表。"""
from __future__ import annotations

from typing import Any, Callable, Dict

from .announcement import announcement_finder as announcement
from .foreign_opinion import opinion_finder as foreign_opinion
from .foreign_report import report_finder as foreign_report
from .investment_calendar import calendar_finder as investment_calendar
from .management_discuss import management_discuss_finder as management_discuss
from .official_account import official_account_finder as official_account
from .opinion import opinion_finder as opinion
from .qa import qa_finder as qa
from .report import report_finder as report
from .report_image import report_image_finder as report_image
from .summary import summary_finder as summary
from .pamirs_summary import pamirs_summary_finder as pamirs_summary
from .get_announcement_types import main as get_announcement_types
from .get_chiefs import get_chiefs as _get_chiefs
from .get_industries import main as get_industries
from .get_institutions import get_institutions as _get_institutions
from .get_regions import main as get_regions
from .get_file import get_file

from .get_announcement_types import ANNOUNCEMENT_CATEGORYS, tree_to_string
from .utils import INDUSTRIES_MAP, REGIONS_MAP, RESEARCH_AREA_MAP

ToolHandler = Callable[..., Any]


def _get_announcement_types(market: str = "cn") -> str:
    valid_types = ["港股公告"] if market == "hk" else ["股票公告"]
    return "\n".join(tree_to_string(ANNOUNCEMENT_CATEGORYS, valid_types=valid_types))


def _get_industries() -> str:
    lines: list[str] = []
    for key, value in INDUSTRIES_MAP.items():
        lines.append(f"# {key}")
        for sub_key, sub_value in value.items():
            lines.append(f"- {sub_key}: {sub_value}")
        lines.append("")
    lines.append("# 研究领域（仅 opinion, summary, pamirs_summary, calendar 支持）")
    for key, value in RESEARCH_AREA_MAP.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines).strip()


def _get_regions() -> str:
    return ", ".join(REGIONS_MAP.keys())



ToolHandler = Callable[..., Any]


TOOL_HANDLERS: Dict[str, ToolHandler] = {
    "report": report,
    "summary": summary,
    "pamirs_summary": pamirs_summary,
    "opinion": opinion,
    "announcement": announcement,
    "foreign_report": foreign_report,
    "foreign_opinion": foreign_opinion,
    "official_account": official_account,
    "management_discuss": management_discuss,
    "qa": qa,
    "report_image": report_image,
    "investment_calendar": investment_calendar,
    "get_file": get_file,
    "get_chiefs": _get_chiefs,
    "get_institutions": _get_institutions,
    "get_industries": _get_industries,
    "get_regions": _get_regions,
    "get_announcement_types": _get_announcement_types,
}

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
