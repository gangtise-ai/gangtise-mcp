from typing import List, Optional

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

from .get_announcement_types import ANNOUNCEMENT_CATEGORYS, tree_to_string
from .get_chiefs import get_chiefs as _get_chiefs
from .get_industries import main as _get_industries_cli
from .get_institutions import get_institutions as _get_institutions
from .get_regions import main as _get_regions_cli
from .get_file import get_file
from .search_chief import SEARCH_TOP_DEFAULT
from .search_institution import SEARCH_TOP_DEFAULT as INSTITUTION_SEARCH_TOP_DEFAULT
from .utils import INDUSTRIES_MAP, REGIONS_MAP, RESEARCH_AREA_MAP


def get_announcement_types(market: str = "cn") -> str:
    valid_types = ["港股公告"] if market == "hk" else ["股票公告"]
    return "\n".join(tree_to_string(ANNOUNCEMENT_CATEGORYS, valid_types=valid_types))


def get_industries() -> str:
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


def get_regions() -> str:
    return ", ".join(REGIONS_MAP.keys())


def get_chiefs(
    keyword: str = "",
    name: str = "",
    institution: str = "",
    group: str = "",
    top: int = SEARCH_TOP_DEFAULT,
):
    text, _code = _get_chiefs(
        keyword=keyword,
        name=name,
        institution=institution,
        group=group,
        top=top,
    )
    return text


def get_institutions(
    keyword: str = "",
    category_list: Optional[List[str]] = None,
    top: int = INSTITUTION_SEARCH_TOP_DEFAULT,
):
    text, _code = _get_institutions(
        keyword=keyword,
        category_list=category_list,
        top=top,
    )
    return text

__all__ = [
    "announcement",
    "foreign_opinion",
    "foreign_report",
    "investment_calendar",
    "management_discuss",
    "official_account",
    "opinion",
    "qa",
    "report",
    "report_image",
    "summary",
    "pamirs_summary",
    "get_announcement_types",
    "get_chiefs",
    "get_industries",
    "get_institutions",
    "get_regions",
    "get_file",
]