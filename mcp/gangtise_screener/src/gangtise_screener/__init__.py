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


def screener(
    expression: str,
    universe: Optional[Union[str, List[str]]] = None,
    indicators: Optional[str] = None,
    params: Optional[Union[str, Dict[str, Any]]] = None,
    trade_date: Optional[str] = None,
    report_date: Optional[str] = None,
    dry_run: bool = False,
    output_dir: Optional[str] = None,
) -> str:
    """条件选股：解析范围与指标说法，构造并执行指标选股请求。"""
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


def screener_resolve_indicator(
    phrase: Union[str, List[str]],
    top: int = 5,
) -> str:
    """解析指标说法，返回候选指标编码与参数元数据。"""
    phrases = _split_csv(phrase)
    outs = [resolve_indicator(p, top) for p in phrases]
    payload = outs if len(outs) > 1 else outs[0]
    return format_resolve_indicator_markdown(payload)


def screener_resolve_universe(phrase: Union[str, List[str]]) -> str:
    """解析选股范围说法，返回 sectorId / 证券代码候选。"""
    phrases = _split_csv(phrase)
    payload = resolve_universe(phrases)
    return format_resolve_universe_markdown(payload)


__all__ = [
    "screener",
    "screener_resolve_indicator",
    "screener_resolve_universe",
]
