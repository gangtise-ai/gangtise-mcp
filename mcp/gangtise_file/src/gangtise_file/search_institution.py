"""机构 ID 搜索（open-reference institutions/search）。"""
import re
from typing import List, Optional, Tuple

import requests
from authorization import authorized_request

from .utils import INSTITUTIONS_SEARCH_URL

MATCH_SCORE_THRESHOLD = 0.6
SEARCH_TOP_DEFAULT = 10
SEARCH_TOP_MAX = 10
_INSTITUTION_ID_RE = re.compile(r"^C\d+$", re.IGNORECASE)

CATEGORY_DOMESTIC_BROKER = "domesticBroker"
CATEGORY_FOREIGN_INSTITUTION = "foreignInstitution"
CATEGORY_FOREIGN_OPINION_INSTITUTION = "foreignOpinionInstitution"
CATEGORY_LEAD_INSTITUTION = "leadInstitution"
CATEGORY_OPINION_INSTITUTION = "opinionInstitution"

CATEGORY_LABELS = {
    CATEGORY_DOMESTIC_BROKER: "内资券商",
    CATEGORY_FOREIGN_INSTITUTION: "外资机构",
    CATEGORY_FOREIGN_OPINION_INSTITUTION: "外资机构观点",
    CATEGORY_LEAD_INSTITUTION: "牵头机构",
    CATEGORY_OPINION_INSTITUTION: "观点所属机构",
}

USAGE_PARAM_BROKER_LIST = "brokerList"
USAGE_PARAM_INSTITUTION_LIST = "institutionList"

USAGE_API_DOMESTIC_REPORT = "查询内资研报列表"
USAGE_API_FOREIGN_REPORT = "查询外资研报列表"
USAGE_API_DOMESTIC_OPINION = "查询内资机构观点列表"
USAGE_API_FOREIGN_OPINION = "查询外资机构观点列表"

_KEYWORD_SUFFIXES = ("集团", "股份有限公司", "有限公司", "证券")


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _match_score(item: dict) -> float:
    try:
        return float(item.get("matchScore") or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_keyword(keyword: str) -> str:
    kw = (keyword or "").strip()
    for suffix in _KEYWORD_SUFFIXES:
        if kw.endswith(suffix) and len(kw) > len(suffix):
            return kw[: -len(suffix)].strip()
    return kw


def _matches_usage(
    item: dict,
    param_name: Optional[str] = None,
    api_name: Optional[str] = None,
) -> bool:
    if not param_name and not api_name:
        return True
    scopes = item.get("usageScopes") or []
    if not isinstance(scopes, list):
        return False
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        if param_name and scope.get("paramName") != param_name:
            continue
        if api_name and scope.get("apiName") != api_name:
            continue
        return True
    return False


def _filter_by_usage(
    items: List[dict],
    param_name: Optional[str] = None,
    api_name: Optional[str] = None,
) -> List[dict]:
    if not param_name and not api_name:
        return items
    return [item for item in items if _matches_usage(item, param_name, api_name)]


def institution_item_to_row(item: dict) -> dict:
    scopes = item.get("usageScopes") or []
    if not isinstance(scopes, list):
        scopes = []
    return {
        "id": str(item.get("institutionId") or "").strip(),
        "name": str(item.get("institutionName") or "").strip(),
        "category": str(item.get("category") or "").strip(),
        "usageScopes": scopes,
        "matchScore": _match_score(item),
    }


def search_institutions(
    headers: dict,
    keyword: str,
    category_list: Optional[List[str]] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[dict], Optional[str]]:
    req_top = max(1, min(int(top), SEARCH_TOP_MAX))
    payload: dict = {"keyword": keyword.strip(), "top": req_top}
    categories = [str(x).strip() for x in (category_list or []) if str(x).strip()]
    if categories:
        payload["categoryList"] = categories
    try:
        r = authorized_request("POST", 
            INSTITUTIONS_SEARCH_URL, headers=headers, json=payload, timeout=120
        )
        if r.status_code != 200:
            return [], f"机构检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"机构检索请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"机构检索接口错误: {_api_error_message(body)}"

    data = body.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return [], "机构检索返回 list 格式异常"
    return items, None


def _search_with_fallback(
    headers: dict,
    keyword: str,
    category_list: Optional[List[str]],
    usage_param_name: Optional[str],
    usage_api_name: Optional[str],
    top: int,
) -> Tuple[List[dict], Optional[str]]:
    keywords = [keyword.strip()]
    normalized = _normalize_keyword(keyword)
    if normalized and normalized not in keywords:
        keywords.append(normalized)

    category_attempts: List[Optional[List[str]]] = []
    if category_list:
        category_attempts.append(category_list)
    category_attempts.append(None)

    for kw in keywords:
        for categories in category_attempts:
            items, err = search_institutions(headers, kw, categories, top)
            if err:
                return [], err
            filtered = _filter_by_usage(items, usage_param_name, usage_api_name)
            if filtered:
                return filtered, None
    return [], None


def _filter_strong_candidates(items: List[dict]) -> List[dict]:
    out = [item for item in items if _match_score(item) > MATCH_SCORE_THRESHOLD]
    out.sort(key=_match_score, reverse=True)
    return out


def _name_contains_keyword(name: str, keyword: str) -> bool:
    n = (name or "").strip()
    kw = (keyword or "").strip()
    if not n or not kw:
        return False
    if kw in n:
        return True
    nkw = _normalize_keyword(kw)
    if nkw and nkw != kw and nkw in n:
        return True
    return False


def _pick_auto_candidate(candidates: List[dict], keyword: str) -> Optional[dict]:
    if len(candidates) == 1:
        return candidates[0]
    kw = (keyword or "").strip()
    if kw:
        exact = [
            c
            for c in candidates
            if str(c.get("institutionName") or "").strip() == kw
        ]
        if len(exact) == 1:
            return exact[0]
        for probe in (kw, _normalize_keyword(kw)):
            if not probe:
                continue
            substr = [
                c
                for c in candidates
                if _name_contains_keyword(str(c.get("institutionName") or ""), probe)
            ]
            if len(substr) == 1:
                return substr[0]
    perfect = [c for c in candidates if _match_score(c) == 1.0]
    if len(perfect) == 1:
        return perfect[0]
    return None


def format_institution_candidates(candidates: List[dict], keyword: str) -> str:
    lines = [
        f"关键词「{keyword}」匹配到 {len(candidates)} 家机构，"
        "请确认后使用 institutionId 或更精确的名称：\n"
    ]
    for c in candidates:
        iid = c.get("institutionId") or ""
        name = c.get("institutionName") or ""
        category = c.get("category") or ""
        label = CATEGORY_LABELS.get(category, category)
        score = _match_score(c)
        scopes = c.get("usageScopes") or []
        scope_txt = "；".join(
            f"{s.get('apiName')}/{s.get('paramName')}"
            for s in scopes
            if isinstance(s, dict)
        )
        line = f"- institutionId={iid} | {name} | {label} | matchScore={score:.2f}"
        if scope_txt:
            line += f" | {scope_txt}"
        lines.append(line)
    return "\n".join(lines)


def resolve_institution_keyword(
    headers: dict,
    keyword: str,
    category_list: Optional[List[str]] = None,
    usage_param_name: Optional[str] = None,
    usage_api_name: Optional[str] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    解析机构关键词。
    返回 (institution_id, message, is_candidates)：
    - 已唯一确定：(id, None, False)
    - 多候选待确认：(None, 候选列表文本, True)
    - 失败：(None, 错误说明, False)
    """
    kw = (keyword or "").strip()
    if not kw:
        return None, "机构关键词为空", False

    if _INSTITUTION_ID_RE.match(kw):
        return kw.upper(), None, False

    items, err = _search_with_fallback(
        headers, kw, category_list, usage_param_name, usage_api_name, top
    )
    if err:
        return None, err, False

    candidates = _filter_strong_candidates(items)
    if not candidates:
        scope_hint = ""
        if usage_api_name:
            scope_hint = f"（适用接口：{usage_api_name}）"
        return None, f"未找到与「{kw}」匹配的相关机构{scope_hint}", False

    picked = _pick_auto_candidate(candidates, kw)
    if picked is None:
        return None, format_institution_candidates(candidates, kw), True

    institution_id = str(picked.get("institutionId") or "").strip()
    if not institution_id:
        return None, "机构检索结果缺少 institutionId", False
    return institution_id, None, False


def resolve_institution_token(
    token: str,
    headers: dict,
    category_list: Optional[List[str]] = None,
    usage_param_name: Optional[str] = None,
    usage_api_name: Optional[str] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """将 institutionId 或机构名称关键词解析为 institutionId。"""
    raw = (token or "").strip()
    if not raw:
        return None, "机构标识为空", False
    if _INSTITUTION_ID_RE.match(raw):
        return raw.upper(), None, False
    return resolve_institution_keyword(
        headers,
        raw,
        category_list,
        usage_param_name,
        usage_api_name,
        top,
    )


def resolve_institution_tokens(
    tokens: List[str],
    headers: dict,
    category_list: Optional[List[str]] = None,
    usage_param_name: Optional[str] = None,
    usage_api_name: Optional[str] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[str], List[str], Optional[str]]:
    """批量解析机构标识，返回 (institution_ids, notes, candidates_message)。"""
    if not tokens:
        return [], [], None

    resolved: List[str] = []
    notes: List[str] = []

    for raw in tokens:
        token = raw.strip()
        if not token:
            continue
        institution_id, msg, is_candidates = resolve_institution_token(
            token,
            headers,
            category_list,
            usage_param_name,
            usage_api_name,
            top,
        )
        if is_candidates:
            return [], [], msg
        if institution_id:
            if institution_id not in resolved:
                resolved.append(institution_id)
        elif msg:
            notes.append(f"机构「{token}」：{msg}")

    return resolved, notes, None
