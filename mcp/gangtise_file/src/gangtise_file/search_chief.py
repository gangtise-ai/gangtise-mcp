"""首席 ID 搜索（open-reference chiefs/search）。"""
import re
from typing import List, Optional, Tuple

import requests

from .utils import authorized_request, CHIEFS_SEARCH_URL

MATCH_SCORE_THRESHOLD = 0.6
SEARCH_TOP_DEFAULT = 10
SEARCH_TOP_MAX = 10
_CHIEF_ID_RE = re.compile(r"^P\d+$", re.IGNORECASE)


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _match_score(item: dict) -> float:
    try:
        return float(item.get("matchScore") or 0)
    except (TypeError, ValueError):
        return 0.0


def chief_item_to_row(item: dict) -> dict:
    return {
        "id": str(item.get("chiefId") or "").strip(),
        "name": str(item.get("chiefName") or "").strip(),
        "institution": str(item.get("institution") or "").strip(),
        "group": str(item.get("team") or "").strip(),
        "matchScore": _match_score(item),
    }


def search_chiefs(
    headers: dict,
    keyword: str,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[dict], Optional[str]]:
    req_top = max(1, min(int(top), SEARCH_TOP_MAX))
    payload = {"keyword": keyword.strip(), "top": req_top}
    try:
        r = authorized_request("POST", CHIEFS_SEARCH_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return [], f"首席检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"首席检索请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"首席检索接口错误: {_api_error_message(body)}"

    data = body.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return [], "首席检索返回 list 格式异常"
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
            if str(c.get("chiefName") or "").strip() == kw
        ]
        if len(exact) == 1:
            return exact[0]
    perfect = [c for c in candidates if _match_score(c) == 1.0]
    if len(perfect) == 1:
        return perfect[0]
    return None


def format_chief_candidates(candidates: List[dict], keyword: str) -> str:
    lines = [
        f"关键词「{keyword}」匹配到 {len(candidates)} 位首席，"
        "请确认后使用 --chiefs 指定 chiefId：\n"
    ]
    for c in candidates:
        cid = c.get("chiefId") or ""
        name = c.get("chiefName") or ""
        inst = c.get("institution") or ""
        team = c.get("team") or ""
        score = _match_score(c)
        lines.append(
            f"- chiefId={cid} | {name} | {inst} / {team} | matchScore={score:.2f}"
        )
    return "\n".join(lines)


def resolve_chief_keyword(
    headers: dict,
    keyword: str,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    解析首席关键词。
    返回 (chief_id, message, is_candidates)：
    - 已唯一确定：(id, None, False)
    - 多候选待确认：(None, 候选列表文本, True)
    - 失败：(None, 错误说明, False)
    """
    kw = (keyword or "").strip()
    if not kw:
        return None, "首席关键词为空", False

    if _CHIEF_ID_RE.match(kw):
        return kw, None, False

    items, err = search_chiefs(headers, kw, top)
    if err:
        return None, err, False

    candidates = _filter_strong_candidates(items)
    if not candidates:
        return None, f"未找到与「{kw}」匹配的相关首席", False

    picked = _pick_auto_candidate(candidates, kw)
    if picked is None:
        return None, format_chief_candidates(candidates, kw), True

    chief_id = str(picked.get("chiefId") or "").strip()
    if not chief_id:
        return None, "首席检索结果缺少 chiefId", False
    return chief_id, None, False


def resolve_chief_token(
    token: str,
    headers: dict,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """将 chiefId 或姓名/机构/团队关键词解析为 chiefId。"""
    raw = (token or "").strip()
    if not raw:
        return None, "首席标识为空", False
    if _CHIEF_ID_RE.match(raw):
        return raw, None, False
    return resolve_chief_keyword(headers, raw, top)
