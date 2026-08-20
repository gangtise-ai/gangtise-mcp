"""公众号 ID 搜索（open-reference officialAccount/search）。"""
import re
from typing import List, Optional, Tuple

import requests

from .utils import authorized_request, OFFICIAL_ACCOUNT_SEARCH_URL

MATCH_SCORE_THRESHOLD = 0.6
SEARCH_TOP_DEFAULT = 10
SEARCH_TOP_MAX = 10
_ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_account_id(token: str) -> bool:
    s = (token or "").strip()
    return bool(s) and _ACCOUNT_ID_RE.fullmatch(s) is not None

ACCOUNT_CATEGORY_LABEL = {
    "listedCompany": "上市公司",
    "broker": "券商团队",
    "government": "政府官方",
    "media": "媒体",
}

ACCOUNT_CATEGORY_CODE_MAP = {v: k for k, v in ACCOUNT_CATEGORY_LABEL.items()}
ACCOUNT_CATEGORY_CODES = frozenset(ACCOUNT_CATEGORY_LABEL.keys())


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _match_score(item: dict) -> float:
    try:
        return float(item.get("matchScore") or 0)
    except (TypeError, ValueError):
        return 0.0


def resolve_account_categories(category_list: Optional[List[str]]) -> List[str]:
    if not category_list:
        return []
    resolved: List[str] = []
    for raw in category_list:
        item = str(raw).strip()
        if not item:
            continue
        if item in ACCOUNT_CATEGORY_CODES:
            code = item
        elif item in ACCOUNT_CATEGORY_CODE_MAP:
            code = ACCOUNT_CATEGORY_CODE_MAP[item]
        else:
            code = item
        if code not in resolved:
            resolved.append(code)
    return resolved


def account_item_to_row(item: dict) -> dict:
    category_raw = item.get("category")
    if category_raw:
        category_display = ACCOUNT_CATEGORY_LABEL.get(str(category_raw), str(category_raw))
    else:
        category_display = ""
    return {
        "公众号ID": str(item.get("accountId") or "").strip(),
        "公众号名称": str(item.get("accountName") or "").strip(),
        "分类": category_display,
        "匹配得分": _match_score(item),
    }


def search_official_accounts(
    headers: dict,
    keyword: str,
    category_list: Optional[List[str]] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[dict], Optional[str]]:
    kw = (keyword or "").strip()
    if not kw:
        return [], "搜索关键词为空"

    req_top = max(1, min(int(top), SEARCH_TOP_MAX))
    payload: dict = {"keyword": kw, "top": req_top}
    categories = resolve_account_categories(category_list)
    if categories:
        payload["category"] = categories

    try:
        r = authorized_request("POST", 
            OFFICIAL_ACCOUNT_SEARCH_URL, headers=headers, json=payload, timeout=120
        )
        if r.status_code != 200:
            return [], f"公众号检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"公众号检索请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"公众号检索接口错误: {_api_error_message(body)}"

    data = body.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return [], "公众号检索返回 list 格式异常"
    return items, None


def _filter_strong_candidates(items: List[dict]) -> List[dict]:
    out = [item for item in items if _match_score(item) > MATCH_SCORE_THRESHOLD]
    out.sort(key=_match_score, reverse=True)
    return out


def _pick_exact_name_match(candidates: List[dict], keyword: str) -> Optional[dict]:
    kw = (keyword or "").strip()
    if not kw:
        return None
    exact = [
        c for c in candidates
        if str(c.get("accountName") or "").strip() == kw
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def format_account_candidates(candidates: List[dict], keyword: str) -> str:
    lines = [
        f"关键词「{keyword}」未找到完全匹配的公众号，以下为候选结果（请确认后使用 --accounts 指定 accountId）：\n"
    ]
    for c in candidates:
        aid = c.get("accountId") or ""
        name = c.get("accountName") or ""
        category = c.get("category") or ""
        cat_label = ACCOUNT_CATEGORY_LABEL.get(str(category), category) if category else "未分类"
        score = _match_score(c)
        lines.append(
            f"- accountId={aid} | {name} | {cat_label} | matchScore={score:.2f}"
        )
    return "\n".join(lines)


def resolve_account_keyword(
    headers: dict,
    keyword: str,
    category_list: Optional[List[str]] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    解析公众号关键词。
    返回 (account_id, message, is_candidates)：
    - 已唯一确定：(id, None, False)
    - 多候选待确认：(None, 候选列表文本, True)
    - 失败：(None, 错误说明, False)
    """
    kw = (keyword or "").strip()
    if not kw:
        return None, "公众号关键词为空", False

    if is_account_id(kw):
        return kw, None, False

    items, err = search_official_accounts(headers, kw, category_list, top)
    if err:
        return None, err, False

    candidates = _filter_strong_candidates(items)
    if not candidates and items:
        candidates = list(items)
        candidates.sort(key=_match_score, reverse=True)

    if not candidates:
        return None, f"未找到与「{kw}」匹配的相关公众号", False

    picked = _pick_exact_name_match(candidates, kw)
    if picked is None:
        return None, format_account_candidates(candidates, kw), True

    account_id = str(picked.get("accountId") or "").strip()
    if not account_id:
        return None, "公众号检索结果缺少 accountId", False
    return account_id, None, False


def resolve_account_token(
    token: str,
    headers: dict,
    category_list: Optional[List[str]] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """将 accountId 或公众号名称关键词解析为 accountId。"""
    raw = (token or "").strip()
    if not raw:
        return None, "公众号标识为空", False
    if is_account_id(raw):
        return raw, None, False
    return resolve_account_keyword(headers, raw, category_list, top)
