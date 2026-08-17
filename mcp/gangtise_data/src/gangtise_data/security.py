import os
import re
import sys
from io import TextIOWrapper
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (SECURITIES_SEARCH_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, remove_html_tags)


_WEIGHT_MATCH = 0.62
_WEIGHT_ASSET = 0.25
_WEIGHT_MARKET = 0.13
_MATCH_SCORE_QUALITY_FLOOR = 0.7

# 与 data_search backend/data/security.py 一致的别名与清理规则（无 detail 分支，不拉取补充接口）
KEYWORD_MAP = {
    "宁王": "宁德时代",
    "寒王": "寒武纪",
    "中金": "中金公司",
    "茅台": "贵州茅台",
    "麦米": "麦格米特",
    "东鹏": "东鹏饮料",
    "上证": "上证指数",
    "深证": "深证成指",
    "阿里": "阿里巴巴",
    "强生": "JNJ.N",
    "LLY": "LLY.N",
    "创业板": "创业板指",
    "创业板指数": "创业板指",
    "恒生": "恒生指数",
    "标普": "标普500",
    "标普500指数": "标普500",
    "纳斯达克": "纳指综合",
    "纳斯达克指数": "纳指综合",
    "纳斯达克综合指数": "纳指综合",
    "富时": "富时100",
    "富时100指数": "富时100",
    "德国DAX指数": "德国DAX",
    "日经": "日经225",
    "日经225指数": "日经225",
    "韩股": "韩国KOSPI指数",
    "韩国KOSPI": "韩国KOSPI指数",
    "韩国综合": "韩国KOSPI指数",
}

_VALID_API_CATEGORIES = frozenset({"stock", "dr", "index", "fund"})


def _drop_weak_matches_if_any_strong(items: List[dict], floor: float = _MATCH_SCORE_QUALITY_FLOOR) -> List[dict]:
    """当列表中至少有一条 matchScore>=floor 时，仅保留 matchScore>=floor 的条目；否则原样返回。"""
    if not items:
        return items
    if not any(float(x.get("matchScore") or 0) >= floor for x in items):
        return items
    return [x for x in items if float(x.get("matchScore") or 0) >= floor]


def _normalize_keyword(raw: str) -> str:
    kw = KEYWORD_MAP.get(raw.strip(), raw.strip())
    kw = kw.upper() if kw else kw
    if kw not in ("新概念能源", "生活概念"):
        kw = kw.replace("行业", "").replace("主题", "").replace("概念", "")
    return kw


def _is_index_by_code(gts_code: str) -> bool:
    """按 gtsCode 后缀/形态判断指数（优先于 A 股后缀）。"""
    u = (gts_code or "").strip().upper()
    if not u:
        return False
    if u.endswith(".SWI") or u.endswith(".CI") or u.endswith(".GT"):
        return True
    if re.match(r"^00\d{4}\.SH$", u):
        return True
    if re.match(r"^399\d{3}\.SZ$", u):
        return True
    if re.match(r"^899\d{3}\.[A-Z]+$", u) or re.match(r"^899\d{3}$", u):
        return True
    return False


def _index_security_type_by_suffix(gts_code: str) -> str:
    """
    指数 security_type 细分（security_category 仍为「指数」）。
    .SH / .SZ / .BJ → 交易所指数；.SWI / .CI → 行业指数；.GT → 概念指数；其余为其他指数。
    """
    u = (gts_code or "").strip().upper()
    if u.endswith(".SH") or u.endswith(".SZ") or u.endswith(".BJ"):
        return "交易所指数"
    if u.endswith(".SWI") or u.endswith(".CI"):
        return "行业指数"
    if u.endswith(".GT"):
        return "概念指数"
    return "其他指数"


def _asset_class_sort_rank(gts_code: str, api_category: str) -> int:
    """同类结果排序：股票（普通）> 存托凭证 > 指数 > 基金 > 其他。"""
    sec_cat, sec_type = _security_type_and_category(gts_code, api_category)
    if sec_cat == "其他":
        return 4
    if sec_cat == "基金":
        return 3
    if sec_cat == "指数":
        return 2
    if sec_type == "存托凭证(DR)" or (api_category or "").strip().lower() == "dr":
        return 1
    return 0


def _market_sort_rank(gts_code: str, asset_rank: int) -> int:
    """同一资产层级内：A股 > 港股 > 其他市场；指数/基金不参与市场细分。"""
    if asset_rank not in (0, 1):
        return 0
    if _is_index_by_code(gts_code):
        return 0
    u = (gts_code or "").strip().upper()
    if u.endswith(".SH") or u.endswith(".SZ") or u.endswith(".BJ"):
        return 0
    if u.endswith(".HK"):
        return 1
    return 2


def _asset_priority_norm(asset_rank: int) -> float:
    """股票(普)>DR>指数>基金>其他 → 映射为 1.0～0.0。"""
    return max(0.0, (4 - asset_rank) / 4.0)


def _market_priority_norm(asset_rank: int, market_rank: int) -> float:
    """A股>港股>其他；指数/基金等不参与市场偏好，取中性 0.5。"""
    if asset_rank not in (0, 1):
        return 0.5
    return max(0.0, (2 - market_rank) / 2.0)


def _weighted_rank_score(item: dict) -> float:
    gts = item.get("gtsCode") or ""
    api = item.get("category") or ""
    match = float(item.get("matchScore") or 0)
    ar = _asset_class_sort_rank(gts, api)
    mr = _market_sort_rank(gts, ar)
    return (
        _WEIGHT_MATCH * match
        + _WEIGHT_ASSET * _asset_priority_norm(ar)
        + _WEIGHT_MARKET * _market_priority_norm(ar, mr)
    )


def _sort_search_results(items: List[dict]) -> List[dict]:
    return sorted(
        items,
        key=lambda x: (
            -_weighted_rank_score(x),
            -float(x.get("matchScore") or 0),
            (x.get("gtsCode") or ""),
        ),
    )


def _sort_exact_matches(exact: List[dict]) -> List[dict]:
    """多地上市等同名/同关键词绝对匹配时：A股 > 港股 > 美股。"""
    return sorted(
        exact,
        key=lambda x: (
            _market_sort_rank(
                x.get("gtsCode") or "",
                _asset_class_sort_rank(x.get("gtsCode") or "", x.get("category") or ""),
            ),
            -float(x.get("matchScore") or 0),
            (x.get("gtsCode") or ""),
        ),
    )


def _exact_match_keywords(keyword: str, kw_normalized: str) -> set:
    keys: set = set()
    for k in (keyword, kw_normalized):
        s = (k or "").strip()
        if not s:
            continue
        keys.add(s)
        keys.add(s.upper())
    return keys


def _is_exact_security_match(item: dict, match_keys: set) -> bool:
    code = (item.get("gtsCode") or "").strip().upper()
    name = remove_html_tags(str(item.get("gtsName") or "").strip())
    name_upper = name.upper()
    for k in match_keys:
        ku = k.upper()
        if code and code == ku:
            return True
        if name and (name == k or name_upper == ku):
            return True
    return False


def _filter_exact_matches(
    items: List[dict], raw_keyword: str, kw_normalized: str
) -> List[dict]:
    match_keys = _exact_match_keywords(raw_keyword, kw_normalized)
    return [x for x in items if _is_exact_security_match(x, match_keys)]


def _rank_search_results(items: List[dict], raw_keyword: str, kw_normalized: str) -> List[dict]:
    """代码或名称绝对匹配时按 A股>港股>美股 排序；否则走综合排序。"""
    exact = _filter_exact_matches(items, raw_keyword, kw_normalized)
    if exact:
        return _sort_exact_matches(exact)
    items = _sort_search_results(items)
    items = _refine_pure_digit_keyword(items, kw_normalized)
    items = _apply_robot_special_order(items, raw_keyword)
    return items


def _security_type_and_category(gts_code: str, api_category: str) -> Tuple[str, str]:
    """
    返回 (security_category, security_type)。
    fund/dr/index 以接口 category 为准；其余按代码后缀分为 A 股 / 港股 / 其他市场。
    """
    cat = (api_category or "").strip().lower()
    if cat == "fund":
        return "基金", "基金"
    if cat == "dr":
        return "股票", "存托凭证(DR)"
    if cat == "index" or _is_index_by_code(gts_code):
        return "指数", _index_security_type_by_suffix(gts_code)
    u = (gts_code or "").strip().upper()
    if u.endswith(".HK"):
        return "股票", "港股"
    if u.endswith(".SH") or u.endswith(".SZ") or u.endswith(".BJ"):
        return "股票", "A股"
    if u.endswith(".US") or u.endswith(".N") or u.endswith(".O") or u.endswith(".A") or u.endswith(".PK") or u.endswith(".OB"):
        return "股票", "美股"
    return "股票", "其他市场"


def _apply_robot_special_order(items: List[dict], keyword_original: str) -> List[dict]:
    """backend 中「机器人」股票/指数顺序的特殊处理：keyword 不含 300024 时与 backend 一致交换顺序。"""
    if "300024" in keyword_original:
        return items
    names = [((it.get("gtsName") or "").strip()) for it in items]
    if "机器人" not in names:
        return items
    idx_stock = next(
        (
            i
            for i, it in enumerate(items)
            if (it.get("gtsName") or "").strip() == "机器人"
            and _security_type_and_category(it.get("gtsCode") or "", it.get("category") or "")[0] == "股票"
        ),
        None,
    )
    idx_index = next(
        (
            i
            for i, it in enumerate(items)
            if (it.get("gtsName") or "").strip() == "机器人"
            and _security_type_and_category(it.get("gtsCode") or "", it.get("category") or "")[0] == "指数"
        ),
        None,
    )
    if idx_stock is None or idx_index is None:
        return items
    out = list(items)
    out[idx_stock], out[idx_index] = out[idx_index], out[idx_stock]
    return out


def _refine_pure_digit_keyword(items: List[dict], keyword_for_match: str) -> List[dict]:
    """纯数字关键词时加强代码前缀匹配（替代 backend 中 thefuzz 逻辑，避免额外依赖）。"""
    if not re.match(r"^\d+$", keyword_for_match):
        return items
    kw = keyword_for_match

    def code_prefix(it: dict) -> str:
        gc = (it.get("gtsCode") or "").strip().upper()
        return gc.split(".")[0] if gc else ""

    exact = [x for x in items if code_prefix(x) == kw]
    if exact:
        rest = [x for x in items if x not in exact]
        return _sort_search_results(exact) + _sort_search_results(rest)
    pref = [x for x in items if code_prefix(x).startswith(kw)]
    if pref:
        rest = [x for x in items if x not in pref]
        return _sort_search_results(pref) + _sort_search_results(rest)
    return items


def _parse_categories_arg(category_arg: Optional[str]) -> Optional[List[str]]:
    if not category_arg or not str(category_arg).strip():
        return None
    parts = [p.strip().lower() for p in re.split(r"[,，\s]+", category_arg) if p.strip()]
    bad = [p for p in parts if p not in _VALID_API_CATEGORIES]
    if bad:
        raise ValueError(f"不支持的 category: {bad}，允许: {sorted(_VALID_API_CATEGORIES)}")
    return parts or None


def security_search(
    keyword: str,
    category: Optional[List[str]] = None,
    top: int = 10,
    headers: Optional[dict] = None,
    output_limit: int = 3,
    **kwargs,
) -> str:
    """
    调用 open-reference 证券搜索接口，返回与 backend security_info（无 detail）结构兼容的 format_response 字符串。
    """
    usage: Dict = {}
    raw_keyword = (keyword or "").strip()
    if not raw_keyword:
        return format_response(
            {"state": "error", "message": "keyword 不能为空", "data": [], "usage": usage},
            "security",
        )

    if headers is None:
        headers = kwargs.get("basic_headers")
    if not isinstance(headers, dict) or not headers:
        headers = get_authorization_headers()

    auth = headers.get("Authorization") or headers.get("authorization")
    if not auth:
        return format_response(
            {"state": "error", "message": "缺少 Authorization，请配置 GTS 授权", "data": [], "usage": usage},
            "security",
        )

    req_top = max(1, min(int(top), 10))
    kw_normalized = _normalize_keyword(raw_keyword)

    payload: dict = {"keyword": kw_normalized, "top": req_top}
    if category:
        payload["category"] = category

    try:
        r = requests.post(SECURITIES_SEARCH_URL, headers=headers, json=payload, timeout=60)
        body = r.json()
    except Exception as e:
        return format_response(
            {"state": "error", "message": f"证券搜索请求失败: {e}", "data": [], "usage": usage},
            "security",
        )

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = body.get("msg") or body.get("message") or r.text[:500]
        return format_response(
            {"state": "error", "message": f"证券搜索接口错误: {msg}", "data": [], "usage": usage},
            "security",
        )

    data_block = body.get("data") or {}
    raw_list = data_block.get("list") or []
    if not raw_list:
        return format_response(
            {"state": "error", "message": "未找到相关证券", "data": [], "usage": usage},
            "security",
        )

    raw_list = _drop_weak_matches_if_any_strong(raw_list)
    if not raw_list:
        return format_response(
            {"state": "error", "message": "未找到相关证券", "data": [], "usage": usage},
            "security",
        )

    items = _rank_search_results(raw_list, raw_keyword, kw_normalized)

    cap = max(1, int(output_limit))
    items = items[:cap]

    securities: List[dict] = []
    for it in items:
        gts_code = (it.get("gtsCode") or "").strip()
        gts_name = (it.get("gtsName") or "").strip()
        api_cat = (it.get("category") or "").strip()
        sec_cat, sec_type = _security_type_and_category(gts_code, api_cat)

        if sec_cat not in ("股票", "行业", "主题", "指数", "基金"):
            sec_cat = "其他"

        row = {
            "security_code": gts_code,
            "security_abbr": remove_html_tags(gts_name) if isinstance(gts_name, str) else gts_name,
            "security_category": sec_cat,
            "security_type": sec_type,
            "industry": None,
            "introduction": None,
            "business_scope": None,
            "investment_logics": None,
        }
        row["info"] = "; ".join(
            [
                f"security_abbr: {row.get('security_abbr', '')}",
                f"security_code: {row.get('security_code', '')}",
                f"security_type: {row.get('security_type', '')}",
            ]
        )
        securities.append(row)

    title = f"{raw_keyword}相关证券"
    df = pd.DataFrame(securities)
    columns = [
        "security_abbr",
        "security_code",
        "security_category",
        "security_type",
        "industry",
        "introduction",
        "business_scope",
        "investment_logics",
    ]
    columns = [c for c in columns if c in df.columns]
    df = df[columns]
    df = df.dropna(axis=1, how="all")

    return format_response(
        {
            "state": "success",
            "message": f"已找到{title}",
            "data": [{"data": df.to_dict(orient="records"), "module": "security", "type": "data"}],
            "usage": usage,
        },
        "security",
    )

def security_search_basic(
    keyword: str,
    category: Optional[List[str]] = None,
    top: int = 10,
    headers: Optional[dict] = None,
    output_limit: int = 3,
    **kwargs,
) -> Dict:
    """
    调用 open-reference 证券搜索接口，返回与 backend security_info（无 detail）结构兼容的 format_response 字符串。
    """
    usage: Dict = {}
    raw_keyword = (keyword or "").strip()
    if not raw_keyword:
        return {"state": "error", "message": "keyword 不能为空", "data": [], "usage": usage}

    if headers is None:
        headers = kwargs.get("basic_headers")
    if not isinstance(headers, dict) or not headers:
        headers = get_authorization_headers()
        
    auth = headers.get("Authorization") or headers.get("authorization")
    if not auth:
        return {"state": "error", "message": "缺少 Authorization，请配置 GTS 授权", "data": [], "usage": usage}

    req_top = max(1, min(int(top), 10))
    kw_normalized = _normalize_keyword(raw_keyword)

    payload: dict = {"keyword": kw_normalized, "top": req_top}
    if category:
        payload["category"] = category

    try:
        r = requests.post(SECURITIES_SEARCH_URL, headers=headers, json=payload, timeout=60)
        body = r.json()
    except Exception as e:
        return {"state": "error", "message": f"证券搜索请求失败: {e}", "data": [], "usage": usage}

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = body.get("msg") or body.get("message") or r.text[:500]
        return {"state": "error", "message": f"证券搜索接口错误: {msg}", "data": [], "usage": usage}

    data_block = body.get("data") or {}
    raw_list = data_block.get("list") or []
    if not raw_list:
        return {"state": "error", "message": "未找到相关证券", "data": [], "usage": usage}

    raw_list = _drop_weak_matches_if_any_strong(raw_list)
    if not raw_list:
        return {"state": "error", "message": "未找到相关证券", "data": [], "usage": usage}

    items = _rank_search_results(raw_list, raw_keyword, kw_normalized)
    exact = _filter_exact_matches(raw_list, raw_keyword, kw_normalized)
    if exact and kwargs.get("return_all_exact"):
        items = _sort_exact_matches(exact)
    else:
        cap = max(1, int(output_limit))
        items = items[:cap]

    securities: List[dict] = []
    for it in items:
        gts_code = (it.get("gtsCode") or "").strip()
        gts_name = (it.get("gtsName") or "").strip()
        api_cat = (it.get("category") or "").strip()
        sec_cat, sec_type = _security_type_and_category(gts_code, api_cat)

        if sec_cat not in ("股票", "行业", "主题", "指数", "基金"):
            sec_cat = "其他"

        row = {
            "security_code": gts_code,
            "security_abbr": remove_html_tags(gts_name) if isinstance(gts_name, str) else gts_name,
            "security_category": sec_cat,
            "security_type": sec_type,
            "industry": None,
            "introduction": None,
            "business_scope": None,
            "investment_logics": None,
        }
        row["info"] = "; ".join(
            [
                f"security_abbr: {row.get('security_abbr', '')}",
                f"security_code: {row.get('security_code', '')}",
                f"security_type: {row.get('security_type', '')}",
            ]
        )
        securities.append(row)

    title = f"{raw_keyword}相关证券"
    df = pd.DataFrame(securities)
    columns = [
        "security_abbr",
        "security_code",
        "security_category",
        "security_type",
        "industry",
        "introduction",
        "business_scope",
        "investment_logics",
    ]
    columns = [c for c in columns if c in df.columns]
    df = df[columns]
    df = df.dropna(axis=1, how="all")

    return {"state": "success", "message": f"已找到{title}", "data": df.to_dict(orient="records"), "usage": usage}

def batch_security_search(
    keywords: List[str],
    category: Optional[List[str]] = None,
    headers: Optional[dict] = None,
    output_limit: int = 1,
    **kwargs,
) -> Dict:
    """
    将多条用户输入解析为 gtsCode（与 gangtise-agent security_clue 一致：已像完整代码则直通，否则走 security_search_basic）。
    返回 dict：state, message, codes, abbrs, types, usage。
    types 与 codes、abbrs 按索引一一对应，值为 security_type（如 A股、港股、交易所指数、行业指数、概念指数、其他指数、存托凭证(DR)、基金、其他市场）。
    """
    usage: Dict = {}
    codes: List[str] = []
    abbrs: List[str] = []
    types: List[str] = []

    if headers is None:
        headers = kwargs.get("basic_headers")
    if not isinstance(headers, dict) or not headers:
        headers = get_authorization_headers()
        
    auth = headers.get("Authorization") or headers.get("authorization")
    if not auth:
        return {
            "state": "error",
            "message": "缺少 Authorization，请配置 GTS 授权",
            "codes": [],
            "abbrs": [],
            "types": [],
            "usage": usage,
        }

    if not keywords:
        return {
            "state": "error",
            "message": "证券列表为空",
            "codes": [],
            "abbrs": [],
            "types": [],
            "usage": usage,
        }

    cat = category if category is not None else ["stock", "dr"]
    code_pattern = re.compile(r"^(\d{6}|[A-Z]?\d+)\.[A-Z]{2,4}$")

    for raw in keywords:
        token = str(raw).strip()
        if not token:
            continue
        u = token.upper()
        if code_pattern.match(u):
            abbr = u
            st_pass = ""
            resp = security_search_basic(
                keyword=u,
                category=cat,
                top=10,
                headers=headers,
                output_limit=1,
                **kwargs,
            )
            for uk, uv in (resp.get("usage") or {}).items():
                usage[uk] = usage.get(uk, 0) + (uv if isinstance(uv, (int, float)) else 0)
            if resp.get("state") == "success":
                for row in resp.get("data") or []:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("security_code") or "").strip().upper()
                    if code == u:
                        abbr = str(row.get("security_abbr") or u).strip() or u
                        st_pass = str(row.get("security_type") or "").strip()
                        break
            if not st_pass:
                _, st_pass = _security_type_and_category(u, "")
            codes.append(u)
            abbrs.append(abbr)
            types.append(st_pass)
            continue

        resp = security_search_basic(
            keyword=token,
            category=cat,
            top=10,
            headers=headers,
            output_limit=output_limit,
            return_all_exact=True,
            **kwargs,
        )
        for uk, uv in (resp.get("usage") or {}).items():
            usage[uk] = usage.get(uk, 0) + (uv if isinstance(uv, (int, float)) else 0)

        if resp.get("state") != "success":
            return {
                "state": "error",
                "message": resp.get("message") or f"证券「{token}」解析失败",
                "codes": [],
                "abbrs": [],
                "types": [],
                "usage": usage,
            }
        rows = resp.get("data") or []
        if not rows:
            return {
                "state": "error",
                "message": f"证券「{token}」未匹配到结果",
                "codes": [],
                "abbrs": [],
                "types": [],
                "usage": usage,
            }
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("security_code") or "").strip()
            abbr = str(row.get("security_abbr") or "").strip()
            if not code:
                return {
                    "state": "error",
                    "message": f"证券「{token}」解析结果缺少代码",
                    "codes": [],
                    "abbrs": [],
                    "types": [],
                    "usage": usage,
                }
            codes.append(code)
            abbrs.append(abbr or code)
            st = str(row.get("security_type") or "").strip()
            if not st:
                _, st = _security_type_and_category(code, "")
            types.append(st)

    if not codes:
        return {
            "state": "error",
            "message": "未解析到有效证券代码",
            "codes": [],
            "abbrs": [],
            "types": [],
            "usage": usage,
        }

    gts_pair_list = [f"{a}({c})" for c, a in zip(codes, abbrs)]
    match_comment = f"<!-- 匹配到的证券/行业：{', '.join(gts_pair_list)} -->\n"
    # CLI：print 到 stdout；MCP：server 会捕获 stdout，也可读 message
    print(match_comment, end="")

    return {
        "state": "success",
        "message": match_comment.rstrip("\n"),
        "codes": codes,
        "abbrs": abbrs,
        "types": types,
        "usage": usage,
    }


def resolved_code_abbr_map(resolved: Dict) -> Dict[str, str]:
    """将 batch_security_search 返回的 codes/abbrs 转为 security_code -> security_abbr 映射。"""
    codes = resolved.get("codes") or []
    abbrs = resolved.get("abbrs") or []
    out: Dict[str, str] = {}
    for code, abbr in zip(codes, abbrs):
        key = str(code).strip().upper()
        if not key:
            continue
        out[key] = str(abbr).strip() or str(code).strip()
    return out

def main():
    import argparse

    try:
        if not check_version():
            update_sh = os.path.join(script_dir, "update.sh")
            print(f"[WARNING] 存在 Gangtise data 版本更新，可以执行 {update_sh} 更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise data 版本失败\n")

    parser = argparse.ArgumentParser(
        description="证券代码搜索（open-reference / securities/search，0 积分）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--keyword", "-k", required=True, help="搜索关键词（名称、代码、拼音等）")
    parser.add_argument(
        "--category",
        "-c",
        default=None,
        help="可选，逗号分隔: stock,dr,index,fund；不传则查全部类",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="请求接口返回条数上限（1–10，与 OpenAPI 一致）",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=3,
        help="落盘/输出条数上限（默认同原 backend 取前 3 条）",
    )
    args = parser.parse_args()

    try:
        cats = _parse_categories_arg(args.category)
    except ValueError as e:
        print(str(e))
        sys.exit(1)

    out = security_search(
        keyword=args.keyword,
        category=cats,
        top=args.top,
        output_limit=args.limit,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
