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


def get_chiefs(**kwargs):
    text, _code = _get_chiefs(**kwargs)
    return text


def get_institutions(**kwargs):
    text, _code = _get_institutions(**kwargs)
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