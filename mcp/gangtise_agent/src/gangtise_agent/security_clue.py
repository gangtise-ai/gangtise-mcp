import os
import re
import sys
from datetime import date, datetime, timedelta
from io import TextIOWrapper
from typing import Any, Dict, List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import authorized_request, GANGTISE_OPENAI_DOMAIN, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra
from .security import security_search_basic

# 未传起止时间时默认前溯窗口（与试用账号权限长度对齐；正式账号可显式传更长区间）
DEFAULT_LOOKBACK_DAYS = 7
POINTS_PER_ITEM = 5

_VALID_QUERY_MODES = {"bySecurity", "byIndustry"}
_QUERY_MODE_ALIASES = {
    "security": "bySecurity",
    "bysecurity": "bySecurity",
    "证券": "bySecurity",
    "industry": "byIndustry",
    "byindustry": "byIndustry",
    "行业": "byIndustry",
}

SECURITY_CLUE_SOURCE_LABEL = {
    "researchReport": "研报",
    "conference": "电话会议（纪要）",
    "announcement": "公告",
    "view": "观点",
}

_VALID_SOURCES = set(SECURITY_CLUE_SOURCE_LABEL.keys())
_SOURCE_ALIASES = {
    "report": "researchReport",
    "research": "researchReport",
    "researchreport": "researchReport",
    "研报": "researchReport",
    "conference": "conference",
    "meeting": "conference",
    "电话会议": "conference",
    "纪要": "conference",
    "announcement": "announcement",
    "公告": "announcement",
    "view": "view",
    "观点": "view",
}

_DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def normalize_query_mode(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in _VALID_QUERY_MODES:
        return s
    low = s.lower()
    if low in _QUERY_MODE_ALIASES:
        return _QUERY_MODE_ALIASES[low]
    for v in _VALID_QUERY_MODES:
        if v.lower() == low:
            return v
    return None


def normalize_security_clue_sources(raw: Optional[List[str]]) -> Optional[List[str]]:
    if not raw:
        return None
    out: List[str] = []
    for item in raw:
        s = str(item).strip()
        if not s:
            continue
        mapped: Optional[str] = None
        if s in _VALID_SOURCES:
            mapped = s
        else:
            low = s.lower()
            mapped = _SOURCE_ALIASES.get(low)
            if not mapped:
                for v in _VALID_SOURCES:
                    if v.lower() == low:
                        mapped = v
                        break
        if mapped and mapped not in out:
            out.append(mapped)
    return out or None


def _validate_time(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if _DATE_ONLY_PATTERN.match(s):
            datetime.strptime(s, "%Y-%m-%d")
            return s
        if _DATETIME_PATTERN.match(s):
            datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return s
    except ValueError:
        return None
    raise ValueError(f"{field_name} 格式错误，仅支持 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss")


def _normalize_codes(raw: Optional[List[str]]) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for item in raw:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def _md_cell(value: Any) -> str:
    s = str(value if value is not None else "").replace("|", "\\|")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.splitlines())
    return s.strip()


def markdown_security_clue_list(data: Dict[str, Any]) -> str:
    total = data.get("total", 0)
    rows = data.get("list", [])
    if not isinstance(rows, list):
        rows = []
    lines: List[str] = [
        f"## 投研线索列表（本页 {len(rows)} 条，接口 total={total}）",
        "",
        "| 来源 | 发布时间 | 标题 | 证券线索 | 行业线索 | 发布者 | 代码 | 名称 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source", "") or "")
        source_label = SECURITY_CLUE_SOURCE_LABEL.get(source, source)
        party = str(row.get("partyName", "") or "")
        party_id = str(row.get("partyId", "") or "")
        publisher = f"{party}（{party_id}）" if party_id else party
        lines.append(
            "| "
            + _md_cell(source_label)
            + " | "
            + _md_cell(row.get("publishTime", ""))
            + " | "
            + _md_cell(row.get("title", ""))
            + " | "
            + _md_cell(row.get("securityContent", ""))
            + " | "
            + _md_cell(row.get("industryContent", ""))
            + " | "
            + _md_cell(publisher)
            + " | "
            + _md_cell(row.get("gtsCode", ""))
            + " | "
            + _md_cell(row.get("gtsName", ""))
            + " |"
        )
    return "\n".join(lines).strip()


def _default_time_window() -> Tuple[str, str]:
    """未传时间时的默认区间：今天往前 DEFAULT_LOOKBACK_DAYS 天（含当天）。"""
    today = date.today()
    start = (today - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d 00:00:00")
    end = today.strftime("%Y-%m-%d 23:59:59")
    return start, end


def _resolve_time_window(
    start_time: Optional[str],
    end_time: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """解析起止时间：均未传则默认近 7 日；只传一侧则补齐另一侧。"""
    start = _validate_time(start_time, "startTime")
    end = _validate_time(end_time, "endTime")
    if not start and not end:
        return _default_time_window()
    if start and not end:
        return start, date.today().strftime("%Y-%m-%d 23:59:59")
    if end and not start:
        end_day = end[:10] if len(end) >= 10 else end
        try:
            end_d = datetime.strptime(end_day, "%Y-%m-%d").date()
        except ValueError:
            end_d = date.today()
        start_d = end_d - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        return start_d.strftime("%Y-%m-%d 00:00:00"), end
    return start, end


def format_security_clue_payload(
    page_from: int = 0,
    page_size: int = 500,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    query_mode: str = "bySecurity",
    gts_code_list: Optional[List[str]] = None,
    source: Optional[List[str]] = None,
) -> Dict[str, Any]:
    size = int(page_size) if page_size is not None else 500
    if size < 1:
        size = 1
    if size > 500:
        size = 500

    frm = int(page_from) if page_from is not None else 0
    if frm < 0:
        frm = 0

    normalized_mode = normalize_query_mode(query_mode)
    if not normalized_mode:
        raise ValueError("queryMode 无效，仅支持 bySecurity 或 byIndustry")

    codes = _normalize_codes(gts_code_list)
    if not codes:
        raise ValueError("securities 不能为空，请至少传入一个证券/行业名称或代码，或传入 all")

    payload: Dict[str, Any] = {
        "from": frm,
        "size": size,
        "queryMode": normalized_mode,
        "gtsCodeList": codes,
    }

    start, end = _resolve_time_window(start_time, end_time)
    if start:
        payload["startTime"] = start
    if end:
        payload["endTime"] = end

    normalized_source = normalize_security_clue_sources(source)
    if normalized_source:
        payload["source"] = normalized_source
    return payload


def normalize_security_clue_response(body: Dict[str, Any]) -> Dict[str, Any]:
    ok = str(body.get("code", "")) == "000000" and body.get("status") is True
    if not ok:
        return {
            "state": "error",
            "message": body.get("msg", "请求失败"),
            "data": [],
            "usage": {},
        }

    raw_data = body.get("data")
    if isinstance(raw_data, dict):
        rows = [{"markdown": markdown_security_clue_list(raw_data), "type": "security-clue-list"}]
        points_cost = len(raw_data.get("list", [])) if isinstance(raw_data.get("list"), list) else 0
        extra_message = (
            f"提示：本次成功返回 {points_cost} 条线索，按 {POINTS_PER_ITEM} 积分/条计费。"
        )
    else:
        rows = [{"markdown": "（无数据）", "type": "security-clue-list"}]
        extra_message = ""

    return {
        "state": "success",
        "message": (body.get("msg", "请求成功") + ("\n" + extra_message if extra_message else "")).strip(),
        "data": [{"data": rows, "module": "agent", "type": "security-clue-list"}],
        "usage": {},
    }


def post_security_clue_list(
    payload: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    url = f"{GANGTISE_OPENAI_DOMAIN}/security-clue/getList"
    headers = get_authorization_headers()
    resp = authorized_request("POST", url, headers=headers, json=payload, timeout=300)
    if resp.status_code != 200:
        return None, resp.text
    try:
        return resp.json(), None
    except Exception as e:
        return None, str(e)


def run_security_clue_list(
    page_from: int = 0,
    page_size: int = 500,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    query_mode: str = "bySecurity",
    securities: Optional[List[str]] = None,
    source: Optional[List[str]] = None,
    output: Optional[str] = None,
) -> str:
    query_mode_map = {
        "bySecurity": "证券",
        "byIndustry": "行业",
    }
    gts_code_list = []
    gts_pair_list = []
    for security in securities:
        if query_mode == "byIndustry":
            security = "申万" + security
            security_search_response = security_search_basic(security, output_limit=1, category=["index"])
        else:
            security_search_response = security_search_basic(security, output_limit=1, category=["stock", "dr"])
        if security_search_response["state"] != "success":
            return format_response(security_search_response, "security_clue_list", output=output)
        security_info = security_search_response["data"][0]
        if not security_info:
            return format_response(
                {"state": "error", "message": f"证券 {security} 不存在", "data": [], "usage": {}},
                "security_clue_list",
                output=output,
            )
        gts_code_list.append(security_info.get("security_code", ""))
        gts_pair_list.append(f"{security_info.get('security_code', '')}({security_info.get('security_abbr', '')})")
    print(f"<!-- 匹配到的证券/行业：{', '.join(gts_pair_list)} -->\n")
    try:
        payload = format_security_clue_payload(
            page_from=page_from,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
            query_mode=query_mode,
            gts_code_list=gts_code_list,
            source=source,
        )
    except ValueError as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "security_clue_list",
            output=output,
        )

    body, err = post_security_clue_list(payload=payload)
    if err is not None:
        return format_response(
            {"state": "error", "message": err, "data": [], "usage": {}},
            "security_clue_list",
            output=output,
        )
    if body is None:
        return format_response(
            {"state": "error", "message": "空响应", "data": [], "usage": {}},
            "security_clue_list",
            output=output,
        )
    normalized = normalize_security_clue_response(body)
    return format_response(normalized, "security_clue_list", output=output)


def _normalize_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    result: List[str] = []
    for item in str(raw).replace("，", ",").split(","):
        value = item.strip()
        if value and value not in result:
            result.append(value)
    return result or None


def main():
    import argparse

    try:
        if not check_version():
            print(f"[WARNING] 存在 Gangtise agent 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise agent 版本失败\n")

    parser = argparse.ArgumentParser(
        description="Gangtise Security Clue OpenAPI 调用入口",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--page-from", type=int, default=0, help="分页 from（从 0 开始）")
    parser.add_argument("--page-size", type=int, default=500, help="分页 size（最大 500）")
    parser.add_argument(
        "-st",
        "--start-time",
        default=None,
        help=f"开始时间 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss；与 -et 均不传时默认近 {DEFAULT_LOOKBACK_DAYS} 日",
    )
    parser.add_argument(
        "-et",
        "--end-time",
        default=None,
        help=f"结束时间 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss；与 -st 均不传时默认近 {DEFAULT_LOOKBACK_DAYS} 日",
    )
    parser.add_argument(
        "-q",
        "--query-mode",
        default="bySecurity",
        help="查询方式：bySecurity(证券) 或 byIndustry(行业)",
    )
    parser.add_argument(
        "--securities",
        default=None,
        help="证券/行业名称或代码列表，逗号分隔；如 贵州茅台,五粮液 或 农林牧渔,有色金属 或 all",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="来源筛选多选，逗号分隔：researchReport,conference,announcement,view",
    )
    parser.add_argument("-o", "--output", default=None, help="结果保存路径")
    args = parser.parse_args()

    out = run_security_clue_list(
        page_from=args.page_from,
        page_size=args.page_size,
        start_time=args.start_time,
        end_time=args.end_time,
        query_mode=args.query_mode,
        securities=_normalize_list(args.securities),
        source=_normalize_list(args.source),
        output=args.output,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
