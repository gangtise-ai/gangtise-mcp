import os
import sys
from typing import List, Optional
import datetime
import requests
from io import TextIOWrapper

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, DOWNLOAD_DEFAULT, DOWNLOAD_TYPE_DEFAULT, INDUSTRIES_MAP, PAMIRS_SUMMARY_URL, RESEARCH_AREA_MAP, check_version, format_response, get_authorization_headers, match_best, remove_html_tags, resolve_result_limit)
from .get_file import download_files
from .security import batch_security_search

PAMIRS_MARKET_CODE_MAP = {
    "A股": "aShares",
    "港股": "hkStocks",
    "美股中概": "usChinaConcept",
    "美股": "usStocks",
}

PAMIRS_MARKET_LABEL = {
    "aShares": "A股",
    "hkStocks": "港股",
    "usChinaConcept": "美股中概",
    "usStocks": "美股",
}

PAMIRS_CATEGORY_CODE_MAP = {
    "公司分析": "companyAnalysis",
    "行业分析": "industryAnalysis",
}

PAMIRS_CATEGORY_LABEL = {
    "companyAnalysis": "公司分析",
    "industryAnalysis": "行业分析",
}


def _format_time_range(start_date: str = None, end_date: str = None):
    """帕米尔接口 startTime/endTime 为字符串（yyyy-MM-dd HH:mm:ss），非毫秒时间戳。"""
    start_time = None
    end_time = None
    if start_date:
        start_time = f"{start_date} 00:00:00"
    if end_date:
        end_time = f"{end_date} 23:59:59"
    return start_time, end_time


def _format_time_value(raw) -> str:
    if raw is None or raw == "":
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float)) and raw:
        s = str(int(raw))
        if len(s) == 13:
            return datetime.datetime.fromtimestamp(raw / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if len(s) == 10:
            return datetime.datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M:%S")
    return str(raw)


def _display_codes(raw_list, label_map: dict) -> str:
    if not isinstance(raw_list, list):
        return ""
    parts = []
    for x in raw_list:
        if not x:
            continue
        code = str(x)
        parts.append(label_map.get(code, code))
    return "、".join(parts)


def _format_pamirs_item(summaries: List[dict]) -> List[dict]:
    _results = []
    for summary in summaries:
        publish_time = _format_time_value(summary.get("publishTime"))
        summary_time = _format_time_value(summary.get("summaryTime"))
        file_time = publish_time or summary_time

        security_items = summary.get("securityList") or []
        stock_display_parts: List[str] = []
        for s in security_items:
            if not isinstance(s, dict):
                continue
            code = (s.get("securityCode") or "").strip()
            name = (s.get("securityName") or "").strip()
            if code and name:
                display = f"{name}({code})"
            elif code:
                display = code
            elif name:
                display = name
            else:
                continue
            if display not in stock_display_parts:
                stock_display_parts.append(display)
        stock_display = "、".join(stock_display_parts)

        research_items = summary.get("researchAreaList") or []
        research_parts: List[str] = []
        for r in research_items:
            if not isinstance(r, dict):
                continue
            name = (r.get("researchAreaName") or "").strip()
            rid = (r.get("researchAreaId") or "").strip()
            if name and rid:
                display = f"{name}({rid})"
            elif name:
                display = name
            elif rid:
                display = rid
            else:
                continue
            if display not in research_parts:
                research_parts.append(display)

        concept_items = summary.get("conceptList") or []
        concept_parts: List[str] = []
        for c in concept_items:
            if not isinstance(c, dict):
                continue
            name = (c.get("conceptName") or "").strip()
            cid = (c.get("conceptId") or "").strip()
            if name and cid:
                display = f"{name}({cid})"
            elif name:
                display = name
            elif cid:
                display = cid
            else:
                continue
            if display not in concept_parts:
                concept_parts.append(display)

        item = {
            "标题": remove_html_tags(summary.get("title", "") or ""),
            "文件时间": file_time,
            "纪要时间": summary_time,
            "分类": _display_codes(summary.get("categoryList") or [], PAMIRS_CATEGORY_LABEL),
            "摘要": remove_html_tags(summary.get("brief", "") or ""),
            "关联股票": stock_display,
            "研究方向": "、".join(research_parts),
            "主题概念": "、".join(concept_parts),
            "市场": _display_codes(summary.get("marketList") or [], PAMIRS_MARKET_LABEL),
            "类型": "帕米尔专家纪要",
            "类型ID": str(summary.get("summaryId") or ""),
        }
        _results.append(item)
    return _results


def _resolve_industries(industries: List[str]) -> List[str]:
    if not industries:
        return []
    all_industries = {}
    for key, value in INDUSTRIES_MAP.items():
        all_industries.update(value.copy())
    all_industries.update(RESEARCH_AREA_MAP.copy())
    results = []
    for industry in industries:
        result = match_best(industry, all_industries.keys())
        if result and result not in results:
            results.append(str(all_industries[result]))
    return results


def _resolve_code_list(
    raw_items: Optional[List[str]],
    label_to_code_map: dict,
) -> List[str]:
    if not raw_items:
        return []

    resolved_codes: List[str] = []
    for raw_item in raw_items:
        if not raw_item:
            continue

        item = raw_item.strip()
        if not item:
            continue

        if item in label_to_code_map:
            resolved_code = label_to_code_map[item]
            if resolved_code not in resolved_codes:
                resolved_codes.append(resolved_code)
            continue

        if item not in resolved_codes:
            resolved_codes.append(item)

    return resolved_codes


def _resolve_market_list(market_list: Optional[List[str]]) -> List[str]:
    return _resolve_code_list(market_list, PAMIRS_MARKET_CODE_MAP)


def _resolve_category_list(category_list: Optional[List[str]]) -> List[str]:
    return _resolve_code_list(category_list, PAMIRS_CATEGORY_CODE_MAP)


def _clean_keyword(
    keyword: str,
    securities=None,
    industries=None,
    market_list=None,
    category_list=None,
) -> str:
    if not keyword:
        return ""
    keyword = (
        keyword.replace("[", "").replace("]", "")
        .replace("、", " ").replace("，", " ")
        .replace(", ", " ").replace(",", " ")
    )
    keyword = (
        keyword.replace("的纪要", "").replace("的专家纪要", "")
        .replace("的帕米尔纪要", "").replace("帕米尔纪要", "")
        .replace("专家纪要", "").replace("纪要", "")
    )
    for items in [securities, industries, market_list, category_list]:
        if items:
            for item in items:
                keyword = keyword.replace(item, "")
    return keyword.strip()


def _fetch_pamirs_summaries(headers, payload_base, keyword, search_type, rank_type, limit):
    max_page_size = 50
    all_results = []
    offset = 0
    remaining = limit

    while remaining > 0:
        page_size = min(remaining, max_page_size)
        data = {**payload_base, "from": offset, "size": page_size}
        if keyword:
            data["keyword"] = keyword
            data["searchType"] = search_type
        if rank_type:
            data["rankType"] = rank_type

        response = authorized_request("POST", PAMIRS_SUMMARY_URL, headers=headers, json=data)
        if response.status_code != 200:
            if all_results:
                return all_results, response.text.replace("\n", " ").replace("\r", " ").strip()
            return None, response.text.replace("\n", " ").replace("\r", " ").strip()
        result = response.json()

        if result.get("code") not in [200, "000000"] and result.get("status") is not True:
            return None, result.get("msg", "请求失败").replace("\n", " ").replace("\r", " ").strip()

        summary_data = result.get("data", {})
        summaries = summary_data.get("list") or []
        if not summaries:
            break

        all_results.extend(_format_pamirs_item(summaries))

        if len(summaries) < page_size:
            break

        offset += page_size
        remaining -= len(summaries)

    return all_results, None


def pamirs_summary_finder(
    keyword: str = "",
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    industries: Optional[List[str]] = None,
    market_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    search_type: int = 1,
    rank_type: int = 1,
    limit: Optional[int] = None,
    download: bool = False,
    download_types: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    try:
        limit = resolve_result_limit(limit, download, "pamirs_summary")
        headers = get_authorization_headers()

        industry_ids = _resolve_industries(industries) if industries else []
        resolved_market_list = _resolve_market_list(market_list)
        resolved_category_list = _resolve_category_list(category_list)

        securities_input = list(securities) if securities else []
        if securities_input:
            tokens = [str(s).strip() for s in securities_input if str(s).strip()]
            resolved = batch_security_search(
                tokens, category=["stock", "dr"], headers=headers, output_limit=1
            )
            if resolved.get("state") != "success":
                return format_response(
                    {"state": "error", "message": resolved.get("message") or "证券解析失败"},
                    "pamirs_summary",
                )
            securities = resolved["codes"]
        else:
            securities = None

        if securities and industries:
            industry_ids = []

        start_time, end_time = _format_time_range(start_date, end_date)

        keyword_str = _clean_keyword(
            keyword=keyword,
            securities=securities_input if securities_input else None,
            industries=industries,
            market_list=resolved_market_list,
            category_list=resolved_category_list,
        )

        payload_base = {}
        if start_time:
            payload_base["startTime"] = start_time
        if end_time:
            payload_base["endTime"] = end_time
        if securities:
            payload_base["securityList"] = securities
        if industry_ids:
            payload_base["researchAreaList"] = industry_ids
        if resolved_category_list:
            payload_base["categoryList"] = resolved_category_list
        if resolved_market_list:
            payload_base["marketList"] = resolved_market_list

        part_error_message = ""
        all_results, err = _fetch_pamirs_summaries(
            headers, payload_base, keyword_str, search_type, rank_type, limit
        )
        if err and not all_results:
            return format_response({"state": "error", "message": err}, "pamirs_summary")
        elif err and all_results:
            part_error_message = f"未完整获取全部结果，错误信息：{err}"

        if not all_results and keyword_str and search_type != 2:
            all_results, err = _fetch_pamirs_summaries(
                headers, payload_base, keyword_str, 2, rank_type, limit
            )
            if err and not all_results:
                return format_response({"state": "error", "message": err}, "pamirs_summary")
            elif err and all_results:
                part_error_message = f"未完整获取全部结果，错误信息：{err}"

        if not all_results:
            return format_response(
                {"state": "error", "message": "未找到相关帕米尔专家纪要，建议修改查询条件", "data": []},
                "pamirs_summary",
            )

        all_results = all_results[:limit]

        additional_message = None
        if download:
            additional_message = download_files(
                all_results, "pamirs_summary", output_dir, download_types=download_types
            ) + ("\n\n" + part_error_message if part_error_message else "")

        response_data = {
            "state": "success",
            "message": "已找到相关帕米尔专家纪要",
            "data": [{"data": all_results, "module": "pamirs_summary", "type": "files"}],
        }
        return format_response(response_data, "pamirs_summary", additional_message=additional_message)
    except Exception as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "pamirs_summary",
        )


def _parse_str_list(raw: str) -> Optional[List[str]]:
    if not raw:
        return None
    items = [
        x.strip()
        for x in raw.replace("，", ",").split(",")
        if x.strip()
    ]
    return items or None


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="帕米尔专家纪要检索：检索帕米尔牵头机构下的专家纪要。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-k", "--keyword", default="", help="检索查询关键词，可为空")
    parser.add_argument("-sd", "--start-date", default="", help="开始日期，格式YYYY-MM-DD")
    parser.add_argument("-ed", "--end-date", default="", help="结束日期，格式YYYY-MM-DD")
    parser.add_argument(
        "-l",
        "--limit",
        default=None,
        type=int,
        help="返回条数上限；不传时用检索默认，开启 -d 下载时默认 5",
    )
    parser.add_argument(
        "--securities",
        default="",
        help="证券代码列表，逗号分隔，可为证券名称、代码或拼音首字母",
    )
    parser.add_argument(
        "--industries",
        default="",
        help="研究方向/行业列表，逗号分隔；可选值见 get_industries.py",
    )
    parser.add_argument(
        "--category-list",
        default="",
        help="纪要类别，逗号分隔（companyAnalysis/industryAnalysis），也可传中文（公司分析/行业分析）",
    )
    parser.add_argument(
        "--market-list",
        default="",
        help="市场类别，逗号分隔（aShares/hkStocks/usChinaConcept/usStocks），也可传中文（A股/港股/美股中概/美股）",
    )
    parser.add_argument(
        "--search-type",
        default=1,
        type=int,
        help="搜索类型：1-标题搜索 2-全文搜索",
    )
    parser.add_argument(
        "--rank-type",
        default=1,
        type=int,
        help="排序方式：1-综合排序 2-时间倒序",
    )
    parser.add_argument(
        "-d",
        "--download",
        default=DOWNLOAD_DEFAULT,
        type=bool,
        help="是否在检索后自动下载对应纪要文件，默认不下载",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="下载文件保存路径，建议使用绝对路径",
    )
    parser.add_argument(
        "-dt",
        "--download-types",
        default=DOWNLOAD_TYPE_DEFAULT.get("pamirs_summary", "original") or "original",
        help="下载的文件类型，逗号分隔，可选值：original（原始文件）、html",
    )

    args = parser.parse_args()

    keyword = args.keyword or ""
    securities = _parse_str_list(args.securities)
    industries = _parse_str_list(args.industries)
    category_list = _parse_str_list(args.category_list)
    market_list = _parse_str_list(args.market_list)
    start_date = args.start_date or None
    end_date = args.end_date or None
    limit = resolve_result_limit(args.limit, bool(args.download), "pamirs_summary")
    search_type = int(args.search_type or 1)
    rank_type = int(args.rank_type or 1)
    download = args.download or False
    output_dir = args.output_dir or None
    download_types = _parse_str_list(args.download_types)
    if not download and output_dir:
        print(f"[WARNING] 参数 -od/--output-dir 仅在下载文件时有效，已忽略\n")
        output_dir = None

    try:
        if not check_version():
            print(f"[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print(f"[WARNING] 检查 Gangtise skills 版本失败\n")

    out = pamirs_summary_finder(
        keyword=keyword,
        securities=securities,
        start_date=start_date,
        end_date=end_date,
        industries=industries,
        category_list=category_list,
        market_list=market_list,
        search_type=search_type,
        rank_type=rank_type,
        limit=limit,
        download=download,
        output_dir=output_dir,
        download_types=download_types,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors='ignore')
    main()
