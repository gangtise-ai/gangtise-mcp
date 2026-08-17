import os
import sys
import time
from io import TextIOWrapper
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from authorization import authorized_request

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (GANGTISE_AGENT_URL, STOCK_SUMMARY_LIST_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra)
from .security import batch_security_search
from .search_concept import resolve_concept_keyword

CHAINED_AGENT_PAIRS = {
    "viewpoint-debate": ("viewpoint-debate-getid", "viewpoint-debate-getcontent"),
    "earnings-review": ("earnings-review-getid", "earnings-review-getcontent"),
}

CHAIN_POLL_MAX_SECONDS = 600
CHAIN_POLL_INTERVAL_SECONDS = 3
STEP_AGENT_TYPES = {"earnings-review-getid", "earnings-review-getcontent", "viewpoint-debate-getid", "viewpoint-debate-getcontent"}

AGENT_ENDPOINTS = {
    "stock-one-pager": "/one-pager",
    "investment-logic": "/investment-logic",
    "peer-comparison": "/peer-comparison",
    "earnings-review-getid": "/earnings-review-getid",
    "earnings-review-getcontent": "/earnings-review-getcontent",
    "viewpoint-debate-getid": "/viewpoint-debate-getid",
    "viewpoint-debate-getcontent": "/viewpoint-debate-getcontent",
    "theme-tracking": "/theme-tracking",
    "research-outline": "/research-outline",
}

AGENT_METHOD_NAME_MAP = {
    "stock-one-pager": "stock_one_pager",
    "investment-logic": "investment_logic",
    "peer-comparison": "peer_comparison",
    "earnings-review": "earnings_review",
    "earnings-review-getid": "earnings_review_getid",
    "earnings-review-getcontent": "earnings_review_getcontent",
    "viewpoint-debate": "viewpoint_debate",
    "viewpoint-debate-getid": "viewpoint_debate_getid",
    "viewpoint-debate-getcontent": "viewpoint_debate_getcontent",
    "theme-tracking": "theme_tracking",
    "research-outline": "research_outline",
    "stock-one-line-summary": "stock_one_line_summary",
}
SPECIAL_AGENT_TYPES = {"stock-one-line-summary"}
PUBLIC_AGENT_TYPES = sorted(
    [k for k in AGENT_ENDPOINTS.keys() if k not in STEP_AGENT_TYPES]
    + list(CHAINED_AGENT_PAIRS.keys())
    + list(SPECIAL_AGENT_TYPES)
)

SECURITY_REQUIRED_AGENT_TYPES = {
    "stock-one-pager",
    "investment-logic",
    "peer-comparison",
    "research-outline",
    "earnings-review",
}


def _normalize_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    result: List[str] = []
    for item in str(raw).replace("，", ",").split(","):
        value = item.strip()
        if value and value not in result:
            result.append(value)
    return result or None


def _resolve_theme_id(theme_input: Optional[str]) -> Optional[str]:
    if theme_input is None:
        return None
    value = str(theme_input).strip()
    if not value:
        return None
    if value.isdigit():
        return value
    return None


def _resolve_theme_input(
    headers: dict,
    theme_input: Optional[str],
) -> Tuple[Optional[str], Optional[str], bool]:
    """解析 theme-tracking 的主题参数。返回 (theme_id, message, is_candidates)。"""
    value = _resolve_theme_id(theme_input)
    if value:
        return value, None, False
    raw = str(theme_input or "").strip()
    if not raw:
        return None, "theme-tracking 需要参数 theme_id（支持主题 ID 或中文主题名）", False
    return resolve_concept_keyword(headers, raw)


def _format_payload(
    agent_type: str,
    security_code: Optional[str] = None,
    period: Optional[str] = None,
    data_id: Optional[str] = None,
    viewpoint: Optional[str] = None,
    theme_id: Optional[str] = None,
    date: Optional[str] = None,
    types: Optional[List[str]] = None,
) -> Dict:
    if agent_type == "earnings-review":
        return _format_payload(
            "earnings-review-getid",
            security_code=security_code,
            period=period,
            data_id=data_id,
            viewpoint=viewpoint,
            theme_id=theme_id,
            date=date,
            types=types,
        )
    if agent_type == "viewpoint-debate":
        return _format_payload(
            "viewpoint-debate-getid",
            security_code=security_code,
            period=period,
            data_id=data_id,
            viewpoint=viewpoint,
            theme_id=theme_id,
            date=date,
            types=types,
        )
    if agent_type in {"stock-one-pager", "investment-logic", "peer-comparison", "research-outline"}:
        if not security_code:
            raise ValueError(f"{agent_type} 需要参数 security 或 securities")
        return {"securityCode": security_code}
    if agent_type == "earnings-review-getid":
        if not security_code:
            raise ValueError("earnings-review-getid 需要参数 security 或 securities")
        if not period:
            raise ValueError("earnings-review-getid 需要参数 period（示例：2025q3）")
        return {"securityCode": security_code, "period": period}
    if agent_type == "earnings-review-getcontent":
        if not data_id:
            raise ValueError("earnings-review-getcontent 需要参数 data_id")
        return {"dataId": data_id}
    if agent_type == "viewpoint-debate-getid":
        if not viewpoint or not str(viewpoint).strip():
            raise ValueError("viewpoint-debate-getid 需要参数 viewpoint（观点文本，不超过 1000 字）")
        v = str(viewpoint).strip()
        if len(v) > 1000:
            raise ValueError("viewpoint 长度不能超过 1000 字")
        return {"viewpoint": v}
    if agent_type == "viewpoint-debate-getcontent":
        if not data_id:
            raise ValueError("viewpoint-debate-getcontent 需要参数 data_id")
        return {"dataId": data_id}
    if agent_type == "theme-tracking":
        resolved_theme_id = _resolve_theme_id(theme_id)
        if not resolved_theme_id:
            raise ValueError("theme-tracking 需要参数 theme_id（支持主题 ID 或中文主题名）")
        if not date:
            raise ValueError("theme-tracking 需要参数 date（yyyy-MM-dd）")
        if not types:
            raise ValueError("theme-tracking 需要参数 types（morning/night，可逗号分隔）")
        return {"themeId": resolved_theme_id, "date": date, "type": types}
    raise ValueError(f"不支持的 agent-type: {agent_type}")


def _tag_rows_with_security(
    rows: List[Dict],
    security_code: Optional[str] = None,
    security_abbr: Optional[str] = None,
) -> List[Dict]:
    if not security_code and not security_abbr:
        return rows
    tagged = []
    for row in rows:
        item = dict(row)
        if security_code:
            item["securityCode"] = security_code
        if security_abbr:
            item["securityAbbr"] = security_abbr
        tagged.append(item)
    return tagged


def _resolve_security_codes(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
) -> Tuple[List[str], List[str], Optional[str]]:
    tokens: List[str] = []
    for raw in [security, *(securities or [])]:
        if not raw:
            continue
        for item in str(raw).replace("，", ",").split(","):
            value = item.strip()
            if value and value not in tokens:
                tokens.append(value)
    if not tokens:
        return [], [], None

    resolved = batch_security_search(tokens, category=["stock", "dr"], output_limit=1)
    if resolved.get("state") != "success":
        return [], [], resolved.get("message") or "证券解析失败"
    codes = resolved.get("codes") or []
    abbrs = resolved.get("abbrs") or []
    if not codes:
        return [], [], "未解析到有效证券代码"
    return codes, abbrs, None


def _collect_security_tokens(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
) -> List[str]:
    tokens: List[str] = []
    for raw in [security, *(securities or [])]:
        if not raw:
            continue
        for item in str(raw).replace("，", ",").split(","):
            value = item.strip()
            if value and value not in tokens:
                tokens.append(value)
    return tokens


def _resolve_stock_summary_security_list(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
) -> Tuple[Optional[List[str]], Optional[str]]:
    """解析 stock-one-line-summary 的 securityList：仅支持 A 股/港股个股批量，不支持市场标识。"""
    tokens = _collect_security_tokens(security, securities)
    if not tokens:
        return None, "stock-one-line-summary 需要参数 security 或 securities（支持证券代码/名称批量查询，不支持 aShares/hkStocks 等市场标识）"

    resolved = batch_security_search(tokens, category=["stock", "dr"], output_limit=1)
    if resolved.get("state") != "success":
        return None, resolved.get("message") or "证券解析失败"
    codes = resolved.get("codes") or []
    types = resolved.get("types") or []
    supported: List[str] = []
    unsupported: List[str] = []
    for code, typ in zip(codes, types):
        t = (typ or "").strip()
        if t in ("A股", "港股", "存托凭证(DR)"):
            if code and code not in supported:
                supported.append(code)
        elif code:
            unsupported.append(code)
    if not supported:
        if unsupported:
            return None, "本接口仅支持 A 股与港股，未解析到有效证券代码"
        return None, "未解析到有效证券代码"
    return supported, None


def _openapi_stock_one_line_summary(
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
) -> Dict:
    security_list, resolve_err = _resolve_stock_summary_security_list(security, securities)
    if resolve_err:
        return {"state": "error", "message": resolve_err, "data": [], "usage": {}}
    if not security_list:
        return {"state": "error", "message": "securityList 不能为空", "data": [], "usage": {}}

    try:
        resp = authorized_request(
            "POST",
            STOCK_SUMMARY_LIST_URL,
            json={"securityList": security_list},
            timeout=300,
        )
    except Exception as e:
        return {"state": "error", "message": str(e), "data": [], "usage": {}}

    if resp.status_code != 200:
        return {"state": "error", "message": resp.text, "data": [], "usage": {}}
    try:
        body = resp.json()
    except Exception as e:
        return {"state": "error", "message": str(e), "data": [], "usage": {}}

    return _normalize_agent_response("stock-one-line-summary", body)


def _normalize_agent_response(
    agent_type: str,
    body: Dict,
    security_code: Optional[str] = None,
    security_abbr: Optional[str] = None,
) -> Dict:
    ok = str(body.get("code", "")) == "000000" and body.get("status") is True
    if not ok:
        return {
            "state": "error",
            "message": body.get("msg", "请求失败"),
            "data": [],
            "usage": {},
        }

    raw_data = body.get("data")
    rows: List[Dict] = []

    if agent_type in {"earnings-review-getid", "viewpoint-debate-getid"}:
        rows = [{"dataId": body.get("dataId", "")}]
    elif agent_type == "theme-tracking":
        data_list = raw_data if isinstance(raw_data, list) else []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "type": item.get("type", ""),
                    "date": item.get("date", ""),
                    "content": item.get("content", ""),
                }
            )
    elif agent_type == "stock-one-line-summary":
        data_obj = raw_data if isinstance(raw_data, dict) else {}
        items = data_obj.get("list") or []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "securityCode": item.get("securityCode", ""),
                        "securityAbbr": item.get("securityName", ""),
                        "date": item.get("date", ""),
                        "content": item.get("summary", ""),
                    }
                )
    elif isinstance(raw_data, dict):
        rows = [
            {
                "date": raw_data.get("date", ""),
                "content": raw_data.get("content", ""),
            }
        ]
    elif isinstance(raw_data, list):
        for item in raw_data:
            if isinstance(item, dict):
                rows.append({"date": item.get("date", ""), "content": item.get("content", "")})
    else:
        rows = [{"content": str(raw_data) if raw_data is not None else ""}]

    rows = _tag_rows_with_security(rows, security_code, security_abbr)
    message = body.get("msg", "请求成功")
    if agent_type == "stock-one-line-summary" and not rows:
        message = "请求成功，但未找到有个股一句话总结的证券（无总结的不返回、不扣积分）"
    return {
        "state": "success",
        "message": message,
        "data": [{"data": rows, "module": "agent", "type": agent_type}],
        "usage": {},
    }


def _raw_post_agent(agent_type: str, payload: Dict) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    url = f"{GANGTISE_AGENT_URL}{AGENT_ENDPOINTS[agent_type]}"
    resp = authorized_request("POST", url, json=payload, timeout=300)
    if resp.status_code != 200:
        return None, resp.text
    try:
        return resp.json(), None
    except Exception as e:
        return None, str(e)


def _getcontent_response_ready(body: Dict) -> bool:
    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return False
    raw = body.get("data")
    if isinstance(raw, dict):
        return bool(str(raw.get("content", "")).strip())
    return False


def _openapi_agent_chained_result(
    composite_type: str,
    security_code: Optional[str],
    security_abbr: Optional[str],
    period: Optional[str],
    viewpoint: Optional[str],
    theme_id: Optional[str],
    date: Optional[str],
    types: Optional[List[str]],
) -> Dict:
    getid_type, getcontent_type = CHAINED_AGENT_PAIRS[composite_type]

    try:
        payload_getid = _format_payload(
            composite_type,
            security_code=security_code,
            period=period,
            data_id=None,
            viewpoint=viewpoint,
            theme_id=theme_id,
            date=date,
            types=types,
        )
    except Exception as e:
        return {"state": "error", "message": str(e), "data": [], "usage": {}}

    body, err = _raw_post_agent(getid_type, payload_getid)
    if err is not None:
        return {"state": "error", "message": err, "data": [], "usage": {}}
    if body is None:
        return {"state": "error", "message": "空响应", "data": [], "usage": {}}

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return {
            "state": "error",
            "message": body.get("msg", "getId 请求失败"),
            "data": [],
            "usage": {},
        }
    data_id = body.get("data").get("dataId") if not body.get("dataId", None) else body.get("dataId")
    if not data_id:
        return {"state": "error", "message": "getId 响应缺少 dataId", "data": [], "usage": {}}

    deadline = time.monotonic() + CHAIN_POLL_MAX_SECONDS
    last_detail = ""

    while time.monotonic() < deadline:
        payload_content = {"dataId": data_id}
        cbody, cerr = _raw_post_agent(getcontent_type, payload_content)
        if cerr is not None:
            last_detail = cerr
        elif cbody is not None:
            if _getcontent_response_ready(cbody):
                normalized = _normalize_agent_response(
                    getcontent_type,
                    cbody,
                    security_code=security_code,
                    security_abbr=security_abbr,
                )
                if normalized["state"] == "success" and normalized.get("data"):
                    normalized["data"][0]["type"] = composite_type
                return normalized
            last_detail = cbody.get("msg", "内容未就绪或为空")

        time.sleep(CHAIN_POLL_INTERVAL_SECONDS)

    return {
        "state": "error",
        "message": f"轮询 getContent 超时（{CHAIN_POLL_MAX_SECONDS}s），dataId={data_id}。最后状态：{last_detail}",
        "data": [],
        "usage": {},
    }


def _openapi_agent_single_result(
    agent_type: str,
    security_code: Optional[str],
    security_abbr: Optional[str],
    period: Optional[str],
    data_id: Optional[str],
    viewpoint: Optional[str],
    theme_id: Optional[str],
    date: Optional[str],
    types: Optional[List[str]],
) -> Dict:
    try:
        payload = _format_payload(
            agent_type=agent_type,
            security_code=security_code,
            period=period,
            data_id=data_id,
            viewpoint=viewpoint,
            theme_id=theme_id,
            date=date,
            types=types,
        )
    except Exception as e:
        return {"state": "error", "message": str(e), "data": [], "usage": {}}

    body, err = _raw_post_agent(agent_type, payload)
    if err is not None:
        return {"state": "error", "message": err, "data": [], "usage": {}}
    if body is None:
        return {"state": "error", "message": "空响应", "data": [], "usage": {}}
    return _normalize_agent_response(
        agent_type,
        body,
        security_code=security_code,
        security_abbr=security_abbr,
    )


def _run_agent_for_security(
    agent_type: str,
    security_code: Optional[str],
    security_abbr: Optional[str],
    period: Optional[str],
    data_id: Optional[str],
    viewpoint: Optional[str],
    theme_id: Optional[str],
    date: Optional[str],
    types: Optional[List[str]],
) -> Dict:
    if agent_type in CHAINED_AGENT_PAIRS:
        return _openapi_agent_chained_result(
            composite_type=agent_type,
            security_code=security_code,
            security_abbr=security_abbr,
            period=period,
            viewpoint=viewpoint,
            theme_id=theme_id,
            date=date,
            types=types,
        )
    return _openapi_agent_single_result(
        agent_type=agent_type,
        security_code=security_code,
        security_abbr=security_abbr,
        period=period,
        data_id=data_id,
        viewpoint=viewpoint,
        theme_id=theme_id,
        date=date,
        types=types,
    )


def openapi_agent(
    agent_type: str,
    security: Optional[str] = None,
    securities: Optional[List[str]] = None,
    period: Optional[str] = None,
    data_id: Optional[str] = None,
    viewpoint: Optional[str] = None,
    theme_id: Optional[str] = None,
    date: Optional[str] = None,
    types: Optional[List[str]] = None,
    output: Optional[str] = None,
):
    method = AGENT_METHOD_NAME_MAP.get(agent_type, "agent")

    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": [], "usage": {}},
            "agent",
            output=output,
        )

    if agent_type in STEP_AGENT_TYPES:
        return format_response(
            {
                "state": "error",
                "message": f"{agent_type} 仅支持内部串联调用，请使用 earnings-review 或 viewpoint-debate",
                "data": [],
                "usage": {},
            },
            "agent",
            output=output,
        )

    if agent_type not in AGENT_ENDPOINTS and agent_type not in CHAINED_AGENT_PAIRS and agent_type not in SPECIAL_AGENT_TYPES:
        return format_response(
            {"state": "error", "message": f"不支持的 agent-type: {agent_type}", "data": [], "usage": {}},
            "agent",
            output=output,
        )

    if agent_type == "stock-one-line-summary":
        try:
            result = _openapi_stock_one_line_summary(security=security, securities=securities)
            return format_response(result, "stock_one_line_summary", output=output)
        except Exception as e:
            return format_response(
                {"state": "error", "message": str(e), "data": [], "usage": {}},
                "stock_one_line_summary",
                output=output,
            )

    headers = get_authorization_headers()

    resolved_theme_id = theme_id
    if agent_type == "theme-tracking":
        rid, msg, is_candidates = _resolve_theme_input(headers, theme_id)
        if is_candidates:
            return msg
        if not rid:
            return format_response(
                {"state": "error", "message": msg or "主题解析失败", "data": [], "usage": {}},
                "theme_tracking",
                output=output,
            )
        resolved_theme_id = rid

    security_codes: List[str] = []
    security_abbrs: List[str] = []
    if agent_type in SECURITY_REQUIRED_AGENT_TYPES:
        security_codes, security_abbrs, resolve_err = _resolve_security_codes(security, securities)
        if resolve_err:
            return format_response(
                {"state": "error", "message": resolve_err, "data": [], "usage": {}},
                method,
                output=output,
            )
        if not security_codes:
            return format_response(
                {"state": "error", "message": f"{agent_type} 需要参数 security 或 securities", "data": [], "usage": {}},
                method,
                output=output,
            )
    elif security or securities:
        security_codes, security_abbrs, resolve_err = _resolve_security_codes(security, securities)
        if resolve_err:
            return format_response(
                {"state": "error", "message": resolve_err, "data": [], "usage": {}},
                method,
                output=output,
            )

    try:
        if len(security_codes) <= 1:
            code = security_codes[0] if security_codes else None
            abbr = security_abbrs[0] if security_abbrs else None
            result = _run_agent_for_security(
                agent_type=agent_type,
                security_code=code,
                security_abbr=abbr,
                period=period,
                data_id=data_id,
                viewpoint=viewpoint,
                theme_id=resolved_theme_id,
                date=date,
                types=types,
            )
            return format_response(result, method, output=output)

        all_rows: List[Dict] = []
        errors: List[str] = []
        for code, abbr in zip(security_codes, security_abbrs):
            label = f"{abbr}({code})" if abbr else code
            result = _run_agent_for_security(
                agent_type=agent_type,
                security_code=code,
                security_abbr=abbr,
                period=period,
                data_id=data_id,
                viewpoint=viewpoint,
                theme_id=resolved_theme_id,
                date=date,
                types=types,
            )
            if result.get("state") != "success":
                errors.append(f"{label}：{result.get('message', '请求失败')}")
                continue
            for block in result.get("data") or []:
                all_rows.extend(block.get("data") or [])

        if not all_rows:
            msg = "；".join(errors) if errors else "未获取到相关资料"
            return format_response(
                {"state": "error", "message": msg, "data": [], "usage": {}},
                method,
                output=output,
            )

        msg = f"已获取 {len(security_codes)} 只证券的相关资料"
        if errors:
            msg += f"（部分证券未取全：{'；'.join(errors)}）"
        merged = {
            "state": "success",
            "message": msg,
            "data": [{"data": all_rows, "module": "agent", "type": agent_type}],
            "usage": {},
        }
        return format_response(merged, method, output=output)
    except Exception as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            method,
            output=output,
        )


def main():
    import argparse

    try:
        if not check_version():
            print(f"[WARNING] 存在 Gangtise agent 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise agent 版本失败\n")

    parser = argparse.ArgumentParser(
        description="Gangtise Agent OpenAPI 统一调用入口",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-a",
        "--agent-type",
        required=True,
        choices=PUBLIC_AGENT_TYPES,
        help="接口类型：含 earnings-review / viewpoint-debate 时为 getId 后轮询 getContent（最长 600s）；",
    )
    parser.add_argument(
        "--security",
        default=None,
        help="单个证券名称或代码，如 贵州茅台 或 600519.SH",
    )
    parser.add_argument(
        "--securities",
        default=None,
        help="多个证券名称或代码，逗号分隔；与 --security 可同时使用并去重合并。用于需证券的 agent 类型；stock-one-line-summary 仅支持 A 股/港股个股批量查询",
    )
    parser.add_argument(
        "--industry",
        default=None,
        help="行业名称或代码，如 电子 或 000001.SZ",
    )
    parser.add_argument(
        "-p",
        "--period",
        default=None,
        help="报告期（earnings-review），如 2025q3",
    )
    parser.add_argument("-d", "--data-id", default=None, help="内容 dataId（保留参数，当前无对外场景）")
    parser.add_argument(
        "--viewpoint",
        default=None,
        help="观点文本（viewpoint-debate），不超过 1000 字",
    )
    parser.add_argument("-t", "--theme-id", default=None, help="主题 ID 或中文主题名（仅 theme-tracking）")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="日期（仅 theme-tracking），格式 yyyy-MM-dd")
    if datetime.now().hour > 16:
        default_theme_tracking_types = "night"
    else:
        default_theme_tracking_types = "morning"
    parser.add_argument("--type", default=default_theme_tracking_types, help="资讯类型（仅 theme-tracking），morning/night，逗号分隔")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="结果保存路径（当前版本由后端统一管理，本参数暂不生效）",
    )
    args = parser.parse_args()

    out = openapi_agent(
        agent_type=args.agent_type,
        security=args.security,
        securities=_normalize_list(args.securities),
        period=args.period,
        data_id=args.data_id,
        viewpoint=args.viewpoint,
        theme_id=args.theme_id,
        date=args.date,
        types=_normalize_list(args.type),
        output=args.output,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
