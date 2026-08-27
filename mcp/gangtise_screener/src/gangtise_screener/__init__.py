from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from .screener import (
    cmd_run_auto,
    format_resolve_indicator_markdown,
    format_resolve_universe_markdown,
    format_run_markdown,
    resolve_indicator,
)
from .universe_resolve import resolve_universe


def _split_csv(raw: Optional[Union[str, List[str]]]) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
        return items
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    parts: List[str] = []
    for chunk in text.replace("，", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or [text]


def _parse_params(params: Optional[Union[str, Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    raw = str(params).strip()
    if not raw:
        return None
    return json.loads(raw)


def _normalize_action(action: Optional[str]) -> str:
    act = (action or "run").strip().lower().replace("-", "_")
    aliases = {
        "resolveindicator": "resolve_indicator",
        "resolveuniverse": "resolve_universe",
    }
    return aliases.get(act, act)


def screener(
    action: Optional[str] = None,
    expression: Optional[str] = None,
    universe: Optional[Union[str, List[str]]] = None,
    indicators: Optional[str] = None,
    phrase: Optional[Union[str, List[str]]] = None,
    params: Optional[Union[str, Dict[str, Any]]] = None,
    trade_date: Optional[str] = None,
    report_date: Optional[str] = None,
    top: int = 5,
    dry_run: bool = False,
    output_dir: Optional[str] = None,
) -> str:
    """条件选股：action=run 执行选股；resolve_indicator / resolve_universe 用于消歧调试。"""
    act = _normalize_action(action)

    if act == "resolve_indicator":
        phrases = _split_csv(phrase)
        if not phrases:
            return "错误：action=resolve_indicator 时必须提供 phrase（指标说法，逗号分隔可传多个）"
        outs = [resolve_indicator(p, top) for p in phrases]
        payload = outs if len(outs) > 1 else outs[0]
        return format_resolve_indicator_markdown(payload)

    if act == "resolve_universe":
        phrases = _split_csv(phrase)
        if not phrases:
            return "错误：action=resolve_universe 时必须提供 phrase（范围说法，逗号分隔可传多个）"
        payload = resolve_universe(phrases)
        return format_resolve_universe_markdown(payload)

    if act == "run":
        if not expression or not str(expression).strip():
            return "错误：action=run 时必须提供 expression（条件表达式）"
        parsed_params = _parse_params(params)
        uni = _split_csv(universe)
        res = cmd_run_auto(
            universe=uni if uni else ["全A"],
            indicators=indicators,
            expression=expression,
            params=parsed_params,
            trade_date=trade_date,
            report_date=report_date,
            dry_run=dry_run,
            output_dir=output_dir,
        )
        return format_run_markdown(res, output_dir=output_dir)

    return (
        f"错误：未知 action=`{action}`。"
        "可选：run（条件选股，默认）、resolve_indicator（解析指标说法）、resolve_universe（解析范围说法）。"
    )


__all__ = ["screener"]
