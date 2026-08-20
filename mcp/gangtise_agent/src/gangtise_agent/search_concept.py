"""题材 ID 搜索（open-reference concepts/search）。"""
from typing import List, Optional, Tuple

import requests

from .utils import authorized_request, CONCEPT_SEARCH_URL

MATCH_SCORE_THRESHOLD = 0.6
SEARCH_TOP_DEFAULT = 10
SEARCH_TOP_MAX = 10


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _match_score(item: dict) -> float:
    try:
        return float(item.get("matchScore") or 0)
    except (TypeError, ValueError):
        return 0.0


def search_concepts(
    headers: dict,
    keyword: str,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[dict], Optional[str]]:
    req_top = max(1, min(int(top), SEARCH_TOP_MAX))
    payload = {"keyword": keyword.strip(), "top": req_top}
    try:
        r = authorized_request("POST", CONCEPT_SEARCH_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return [], f"题材检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"题材检索请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"题材检索接口错误: {_api_error_message(body)}"

    data = body.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return [], "题材检索返回 list 格式异常"
    return items, None


def _filter_strong_candidates(items: List[dict]) -> List[dict]:
    out = [item for item in items if _match_score(item) > MATCH_SCORE_THRESHOLD]
    out.sort(key=_match_score, reverse=True)
    return out


def _pick_auto_candidate(candidates: List[dict], keyword: str) -> Optional[dict]:
    if len(candidates) == 1:
        return candidates[0]
    kw = (keyword or "").strip()
    if kw:
        exact = [
            c for c in candidates
            if str(c.get("conceptName") or "").strip() == kw
        ]
        if len(exact) == 1:
            return exact[0]
    perfect = [c for c in candidates if _match_score(c) == 1.0]
    if len(perfect) == 1:
        return perfect[0]
    return None


def format_concept_candidates(candidates: List[dict], keyword: str) -> str:
    lines = [
        f"关键词「{keyword}」匹配到 {len(candidates)} 个题材，"
        "请确认后使用 -t/--theme-id 指定 conceptId：\n"
    ]
    for c in candidates:
        lines.append(
            f"- conceptId={c.get('conceptId')} | {c.get('conceptName')}"
        )
    return "\n".join(lines)


def resolve_concept_keyword(
    headers: dict,
    keyword: str,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    解析题材关键词。
    返回 (concept_id, message, is_candidates)：
    - 已唯一确定：(id, None, False)
    - 多候选待确认：(None, 候选列表文本, True)
    - 失败：(None, 错误说明, False)
    """
    kw = (keyword or "").strip()
    if not kw:
        return None, "题材关键词为空", False

    items, err = search_concepts(headers, kw, top)
    if err:
        return None, err, False

    candidates = _filter_strong_candidates(items)
    if not candidates:
        return None, f"未找到与「{kw}」匹配的相关题材", False

    picked = _pick_auto_candidate(candidates, kw)
    if picked is None:
        return None, format_concept_candidates(candidates, kw), True

    concept_id = str(picked.get("conceptId") or "").strip()
    if not concept_id:
        return None, "题材检索结果缺少 conceptId", False
    return concept_id, None, False
