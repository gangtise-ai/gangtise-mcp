from typing import List, Optional

from .agents import openapi_agent
from .hot_topic import run_hot_topic_list as hot_topic
from .security_clue import run_security_clue_list as security_clue

def stock_one_pager(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "stock-one-pager",
        security=security,
        securities=securities,
        output_dir=output_dir,
    )


def investment_logic(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "investment-logic",
        security=security,
        securities=securities,
        output_dir=output_dir,
    )


def peer_comparison(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "peer-comparison",
        security=security,
        securities=securities,
        output_dir=output_dir,
    )


def earnings_review(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    period: Optional[str] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "earnings-review",
        security=security,
        securities=securities,
        period=period,
        output_dir=output_dir,
    )


def viewpoint_debate(
    viewpoint: Optional[str] = None,
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "viewpoint-debate",
        security=security,
        securities=securities,
        viewpoint=viewpoint,
        output_dir=output_dir,
    )


def theme_tracking(
    theme_id: Optional[str] = None,
    date: Optional[str] = None,
    types: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "theme-tracking",
        theme_id=theme_id,
        date=date,
        types=types,
        output_dir=output_dir,
    )


def research_outline(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "research-outline",
        security=security,
        securities=securities,
        output_dir=output_dir,
    )


def stock_one_line_summary(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    return openapi_agent(
        "stock-one-line-summary",
        security=security,
        securities=securities,
        output_dir=output_dir,
    )

__all__ = [
    "stock_one_pager",
    "investment_logic",
    "peer_comparison",
    "earnings_review",
    "viewpoint_debate",
    "theme_tracking",
    "research_outline",
    "stock_one_line_summary",
    "hot_topic",
    "security_clue",
]
