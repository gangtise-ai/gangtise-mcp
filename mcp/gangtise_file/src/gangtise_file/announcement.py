import os
import sys
from typing import List, Optional, Tuple
import datetime
import requests
from io import TextIOWrapper

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (ANNOUNCEMENT_CATEGORY_MAP, COMPANY_ANNOUNCEMENT_URL, DOWNLOAD_DEFAULT, DOWNLOAD_TYPE_DEFAULT, FILE_DEFAULT_LIMIT, FILE_DOWNLOAD_DEFAULT_LIMIT, HK_ANNOUNCEMENT_CATEGORY_MAP, HK_ANNOUNCEMENT_LIST_URL, US_ANNOUNCEMENT_CATEGORY_MAP, US_ANNOUNCEMENT_LIST_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, match_best, remove_html_tags, resolve_result_limit)
from .get_file import download_files
from .security import batch_security_search


def _format_time_range(start_date: str = None, end_date: str = None):
    start_timestamp = None
    end_timestamp = None
    if start_date:
        start_timestamp = int(datetime.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    if end_date:
        end_timestamp = int(
            (datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1)).timestamp() * 1000
        ) - 1
    return start_timestamp, end_timestamp


def _format_time_range_hk_strings(start_date: Optional[str], end_date: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """港股公告列表接口：startTime/endTime 为字符串，兼容 yyyy-MM-dd（自动补全起止时刻）。"""
    st = None
    et = None
    if start_date and str(start_date).strip():
        d = str(start_date).strip()[:10]
        st = f"{d} 00:00:00"
    if end_date and str(end_date).strip():
        d = str(end_date).strip()[:10]
        et = f"{d} 23:59:59"
    return st, et


def _format_announcement_item(announcements: List[dict], doc_type: str = "公司公告") -> List[dict]:
    _results = []
    for ann in announcements:
        publish_time = ann.get("publishTime")
        ann_date = ann.get("announcementDate")
        file_time = ""
        if isinstance(publish_time, (int, float)) and publish_time and len(str(publish_time)) == 13:
            file_time = datetime.datetime.fromtimestamp(publish_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(publish_time, (int, float)) and publish_time and len(str(publish_time)) == 10:
            file_time = datetime.datetime.fromtimestamp(publish_time).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(publish_time, str) and publish_time:
            file_time = publish_time
        elif isinstance(ann_date, (int, float)) and ann_date and len(str(ann_date)) == 13:
            file_time = datetime.datetime.fromtimestamp(ann_date / 1000).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(ann_date, (int, float)) and ann_date and len(str(ann_date)) == 10:
            file_time = datetime.datetime.fromtimestamp(ann_date).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(ann_date, str) and ann_date:
            file_time = ann_date

        primary = ann.get("primaryCategory") or {}
        secondary = ann.get("secondaryCategory") or {}
        primary_name = (primary.get("categoryName") or "").strip() if isinstance(primary, dict) else ""
        secondary_name = (secondary.get("categoryName") or "").strip() if isinstance(secondary, dict) else ""
        category_display = " / ".join([x for x in [primary_name, secondary_name] if x])

        sec_code = (ann.get("securityCode") or "").strip()
        sec_name = (ann.get("securityName") or "").strip()
        sec_display = ""
        if sec_code and sec_name:
            sec_display = f"{sec_name}({sec_code})"
        elif sec_code or sec_name:
            sec_display = sec_code or sec_name
        else:
            sec_list_raw = ann.get("securityList") or []
            if isinstance(sec_list_raw, list) and sec_list_raw:
                parts = []
                for s in sec_list_raw:
                    if not isinstance(s, dict):
                        continue
                    c = (s.get("securityCode") or "").strip()
                    n = (s.get("securityName") or "").strip()
                    if c and n:
                        parts.append(f"{n}({c})")
                    elif c or n:
                        parts.append(c or n)
                sec_display = "、".join(parts)

        title_tr = remove_html_tags(ann.get("titleTranslate", "") or "")
        fn_list = ann.get("fileNameList") or []
        attach_names = ""
        if isinstance(fn_list, list) and fn_list:
            attach_names = "、".join(str(x) for x in fn_list if x)
        fc = ann.get("fileCount")
        item = {
            "标题": remove_html_tags(ann.get("title", "")),
            "文件时间": file_time,
            "所属证券": sec_display,
            "公告类型": category_display,
            "来源": ann.get("sourceName", "") or "",
            "摘要": "",
            "类型": doc_type,
            "类型ID": str(ann.get("announcementId", "") or ""),
        }
        if doc_type == "港股公告":
            item["中文标题"] = title_tr
        if doc_type in ("港股公告", "美股公告"):
            if fc is not None:
                item["附件数量"] = fc
            if attach_names:
                item["附件名称"] = attach_names
        _results.append(item)
    return _results


def _clean_keyword(keyword: str, securities=None) -> str:
    if not keyword:
        return ""
    keyword = (
        keyword.replace("[", "").replace("]", "")
        .replace("、", " ").replace("，", " ")
        .replace(", ", " ").replace(",", " ")
    )
    keyword = (
        keyword.replace("的公告", "").replace("的公司公告", "")
        .replace("公司公告", "").replace("公告", "")
    )
    if securities:
        for item in securities:
            keyword = keyword.replace(item, "")
    return keyword.strip()


def _fetch_announcements(headers, payload_base, keyword, search_type, rank_type, limit):
    """分页获取 A 股公司公告，返回格式化后的结果列表"""
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
        response = authorized_request("POST", COMPANY_ANNOUNCEMENT_URL, headers=headers, json=data, timeout=120)
        if response.status_code != 200:
            if all_results:
                return all_results, response.text.replace("\n", " ").replace("\r", " ").strip()
            return None, response.text.replace("\n", " ").replace("\r", " ").strip()
        result = response.json()

        if result.get("code") not in [200, "000000"] and result.get("status") is not True:
            return None, result.get("msg", "请求失败").replace("\n", " ").replace("\r", " ").strip()

        ann_data = result.get("data", {})
        announcements = ann_data.get("list", [])
        if not announcements:
            break

        all_results.extend(_format_announcement_item(announcements, "公司公告"))

        if len(announcements) < page_size:
            break

        offset += page_size
        remaining -= len(announcements)

    return all_results, None


def _fetch_hk_announcements(headers, payload_base, keyword, search_type, rank_type, limit):
    """分页获取港股公告；searchType、rankType 为必选，与 keyword 无关。"""
    max_page_size = 50
    all_results = []
    offset = 0
    remaining = limit

    while remaining > 0:
        page_size = min(remaining, max_page_size)
        data = {
            **payload_base,
            "from": offset,
            "size": page_size,
            "searchType": int(search_type),
            "rankType": int(rank_type),
        }
        if keyword:
            data["keyword"] = keyword

        response = authorized_request("POST", HK_ANNOUNCEMENT_LIST_URL, headers=headers, json=data, timeout=120)
        if response.status_code != 200:
            if all_results:
                return all_results, response.text.replace("\n", " ").replace("\r", " ").strip()
            return None, response.text.replace("\n", " ").replace("\r", " ").strip()
        result = response.json()

        if str(result.get("code", "")) != "000000" or result.get("status") is not True:
            msg = (result.get("msg") or "请求失败").replace("\n", " ").replace("\r", " ").strip()
            if not all_results:
                return None, msg
            return all_results, msg

        ann_data = result.get("data", {})
        announcements = ann_data.get("list", [])
        if not announcements:
            break

        all_results.extend(_format_announcement_item(announcements, "港股公告"))

        if len(announcements) < page_size:
            break

        offset += page_size
        remaining -= len(announcements)

    return all_results, None


def _fetch_us_announcements(headers, payload_base, keyword, search_type, rank_type, limit):
    """分页获取美股公告；searchType、rankType 为必选，与 keyword 无关。"""
    max_page_size = 50
    all_results = []
    offset = 0
    remaining = limit

    while remaining > 0:
        page_size = min(remaining, max_page_size)
        data = {
            **payload_base,
            "from": offset,
            "size": page_size,
            "searchType": int(search_type),
            "rankType": int(rank_type),
        }
        if keyword:
            data["keyword"] = keyword

        response = authorized_request("POST", US_ANNOUNCEMENT_LIST_URL, headers=headers, json=data, timeout=120)
        if response.status_code != 200:
            if all_results:
                return all_results, response.text.replace("\n", " ").replace("\r", " ").strip()
            return None, response.text.replace("\n", " ").replace("\r", " ").strip()
        result = response.json()

        if str(result.get("code", "")) != "000000" or result.get("status") is not True:
            msg = (result.get("msg") or "请求失败").replace("\n", " ").replace("\r", " ").strip()
            if not all_results:
                return None, msg
            return all_results, msg

        ann_data = result.get("data", {})
        announcements = ann_data.get("list", [])
        if not announcements:
            break

        all_results.extend(_format_announcement_item(announcements, "美股公告"))

        if len(announcements) < page_size:
            break

        offset += page_size
        remaining -= len(announcements)

    return all_results, None


def _resolve_announcement_categories(categories: Optional[List[str]], *, market: str = "cn") -> List[str]:
    if not categories:
        return []
    if market == "hk":
        cat_map = HK_ANNOUNCEMENT_CATEGORY_MAP
    elif market == "us":
        cat_map = US_ANNOUNCEMENT_CATEGORY_MAP
    else:
        cat_map = ANNOUNCEMENT_CATEGORY_MAP
    keys = list(cat_map.keys()) if cat_map else []
    results: List[str] = []
    for c in categories:
        if not c:
            continue
        c = str(c).strip()
        if not c:
            continue
        # 纯 ID（数字/字母混合）直接透传
        if c.isdigit() or c.startswith("ANN") or c.startswith("10"):
            if c not in results:
                results.append(c)
            continue
        if keys:
            matched = match_best(c, keys)
            if matched:
                cid = str(cat_map[matched])
                if cid and cid not in results:
                    results.append(cid)
    return results


def _partition_announcement_by_security_type(
    codes: List[str], types: List[str]
) -> Tuple[List[str], List[str], List[str]]:
    """按 batch_security_search 返回的 security_type 分为港股、A 股、美股公告证券列表。"""
    hk: List[str] = []
    cn: List[str] = []
    us: List[str] = []
    seen_hk: set = set()
    seen_cn: set = set()
    seen_us: set = set()
    for c, t in zip(codes or [], types or []):
        c = (c or "").strip()
        if not c:
            continue
        t = (t or "").strip()
        if t == "港股":
            if c not in seen_hk:
                seen_hk.add(c)
                hk.append(c)
        elif t == "美股":
            if c not in seen_us:
                seen_us.add(c)
                us.append(c)
        elif t in ("A股", "存托凭证(DR)", "其他市场"):
            if c not in seen_cn:
                seen_cn.add(c)
                cn.append(c)
    return hk, cn, us


def _split_limit_across_groups(limit: int, n: int) -> List[int]:
    if n <= 0:
        return []
    q, r = divmod(max(0, int(limit)), n)
    return [q + (1 if i < r else 0) for i in range(n)]


def _adjust_category_ids_for_mixed_query(category_ids: List[str], market: str, is_mixed: bool) -> List[str]:
    """同时查多市场时，纯数字 categoryId 按前缀只发往对应市场，避免混传。"""
    if not category_ids or not is_mixed:
        return list(category_ids or [])
    out: List[str] = []
    for x in category_ids:
        s = str(x).strip()
        if s.isdigit() and len(s) >= 9:
            if s.startswith("103970"):
                if market == "hk":
                    out.append(s)
            elif s.startswith("103980"):
                if market == "us":
                    out.append(s)
            else:
                if market == "cn":
                    out.append(s)
        else:
            out.append(s)
    return out


def _build_announcement_payload_base(
    market: str,
    start_date: Optional[str],
    end_date: Optional[str],
    security_list: Optional[List[str]],
    category_ids: List[str],
) -> dict:
    pb: dict = {}
    if market in ("hk", "us"):
        st_s, et_s = _format_time_range_hk_strings(start_date, end_date)
        if st_s:
            pb["startTime"] = st_s
        if et_s:
            pb["endTime"] = et_s
    else:
        start_timestamp, end_timestamp = _format_time_range(start_date, end_date)
        if start_timestamp:
            pb["startTime"] = start_timestamp
        if end_timestamp:
            pb["endTime"] = end_timestamp
    if security_list:
        pb["securityList"] = list(security_list)
    if category_ids:
        pb["categoryList"] = category_ids
    return pb


_MARKET_LABEL = {"cn": "A 股公司公告", "hk": "港股公告", "us": "美股公告"}


def _fetch_one_announcement_market(
    market: str,
    headers: dict,
    payload_base: dict,
    keyword_str: str,
    search_type: int,
    rank_type: int,
    sub_limit: int,
) -> Tuple[Optional[List[dict]], Optional[str], str]:
    """单市场拉取（含全文回退）。失败且无数据时返回 (None, err, part_msg)。"""
    part_error_message = ""
    if market == "hk":
        fetch_fn = _fetch_hk_announcements
    elif market == "us":
        fetch_fn = _fetch_us_announcements
    else:
        fetch_fn = _fetch_announcements

    all_results, err = fetch_fn(headers, payload_base, keyword_str, search_type, rank_type, sub_limit)
    if err and not all_results:
        return None, err, ""
    if err and all_results:
        part_error_message = f"未完整获取全部结果，错误信息：{err}"
    if not all_results and keyword_str:
        fallback_search = 2
        all_results, err = fetch_fn(headers, payload_base, keyword_str, fallback_search, rank_type, sub_limit)
        if err and not all_results:
            return None, err, ""
        if err and all_results:
            part_error_message = f"未完整获取全部结果，错误信息：{err}"
    return all_results or [], None, part_error_message


def announcement_finder(
    keyword: str = "",
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category_list: Optional[List[str]] = None,
    search_type: int = 1,
    rank_type: int = 1,
    limit: Optional[int] = None,
    download: bool = False,
    output_dir: Optional[str] = None,
    download_types: Optional[List[str]] = None,
):
    try:
        limit = resolve_result_limit(limit, download, "announcement")
        headers = get_authorization_headers()
        
        securities_input = list(securities) if securities else []
        hk_codes: List[str] = []
        cn_codes: List[str] = []
        us_codes: List[str] = []

        if securities_input:
            tokens = [str(s).strip() for s in securities_input if str(s).strip()]
            resolved = batch_security_search(
                tokens,
                category=["stock", "dr", "index"],
                headers=headers,
                output_limit=1,
            )
            if resolved.get("state") != "success":
                return format_response(
                    {
                        "state": "error",
                        "message": resolved.get("message") or "证券解析失败",
                        "data": [],
                    },
                    "announcement", output_dir=output_dir)
            hk_codes, cn_codes, us_codes = _partition_announcement_by_security_type(
                resolved.get("codes") or [],
                resolved.get("types") or [],
            )
            if not hk_codes and not cn_codes and not us_codes:
                return format_response(
                    {
                        "state": "error",
                        "message": "所选证券类型不支持公告检索（如指数、基金等），请改用股票或存托凭证代码",
                        "data": [],
                    },
                    "announcement", output_dir=output_dir)

        # (market, security_list or None)；无证券时仅走 A 股公司公告 keyword 检索
        groups: List[Tuple[str, Optional[List[str]]]] = []
        if securities_input:
            if cn_codes:
                groups.append(("cn", cn_codes))
            if hk_codes:
                groups.append(("hk", hk_codes))
            if us_codes:
                groups.append(("us", us_codes))
        else:
            groups.append(("cn", None))

        is_mixed = len(groups) > 1
        limits = _split_limit_across_groups(limit, len(groups))
        cat_hk = _adjust_category_ids_for_mixed_query(
            _resolve_announcement_categories(category_list, market="hk"),
            "hk",
            is_mixed,
        )
        cat_cn = _adjust_category_ids_for_mixed_query(
            _resolve_announcement_categories(category_list, market="cn"),
            "cn",
            is_mixed,
        )
        cat_us = _adjust_category_ids_for_mixed_query(
            _resolve_announcement_categories(category_list, market="us"),
            "us",
            is_mixed,
        )
        cat_by_market = {"cn": cat_cn, "hk": cat_hk, "us": cat_us}

        keyword_str = _clean_keyword(keyword, securities_input if securities_input else None)

        all_results: List[dict] = []
        part_error_message = ""
        branch_errors: List[str] = []

        for (market, sec_list), sub_limit in zip(groups, limits):
            cat_ids = cat_by_market[market]
            payload_base = _build_announcement_payload_base(market, start_date, end_date, sec_list, cat_ids)
            chunk, err, part = _fetch_one_announcement_market(
                market, headers, payload_base, keyword_str, search_type, rank_type, sub_limit
            )
            if part:
                part_error_message = (part_error_message + "\n" + part).strip() if part_error_message else part
            if err:
                branch_errors.append(f"{_MARKET_LABEL[market]}：{err}")
                continue
            if chunk:
                all_results.extend(chunk)

        if not all_results:
            if branch_errors:
                return format_response(
                    {"state": "error", "message": "；".join(branch_errors), "data": []},
                    "announcement", output_dir=output_dir)
            return format_response(
                {"state": "error", "message": "未找到相关公告，建议修改查询条件", "data": []},
                "announcement", output_dir=output_dir)

        all_results = all_results[:limit]

        additional_message = None
        if download:
            additional_message = download_files(
                all_results, "announcement", output_dir, download_types=download_types
            ) + ("\n\n" + part_error_message if part_error_message else "")

        msg = "已找到相关公告"
        if securities_input and is_mixed:
            markets = [m for m, _ in groups]
            labels = [_MARKET_LABEL[m] for m in markets]
            msg = f"已找到相关公告（已合并 {'、'.join(labels)} 结果）"
        elif securities_input and len(groups) == 1:
            msg = f"已找到相关{_MARKET_LABEL[groups[0][0]]}"

        response_data = {
            "state": "success",
            "message": msg,
            "data": [{"data": all_results, "module": "announcement", "type": "files"}],
        }
        if branch_errors:
            warn = "；".join(branch_errors)
            response_data["message"] = f"{msg}（部分市场未取全：{warn}）"
        return format_response(response_data, "announcement", additional_message=additional_message, output_dir=output_dir)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "announcement", output_dir=output_dir)


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
        description="公告检索：无 --securities 时走 A 股公司公告；有证券时按解析类型自动选 A 股、港股或美股接口（可混选合并结果）。下载见 get_file（公司公告/港股公告/美股公告）。",
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
        help="证券代码列表，逗号分隔，必须为标准证券代码，如 000001.SZ",
    )
    parser.add_argument(
        "--category-list",
        default="",
        help="逗号分隔；含港股证券时栏目名见 utils.HK_ANNOUNCEMENT_CATEGORY_MAP，含美股见 US_ANNOUNCEMENT_CATEGORY_MAP，否则见 ANNOUNCEMENT_CATEGORY_MAP；混选多市场时各自解析。纯数字 ID 在混选时 103970xxx 仅发港股、103980xxx 仅发美股",
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
        help="是否在检索后自动下载对应公司公告文件，默认不下载",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="结果与下载文件保存目录路径，建议使用绝对路径",
    )
    parser.add_argument(
        "-dt",
        "--download-types",
        default=DOWNLOAD_TYPE_DEFAULT.get("announcement", "pdf") or "pdf",
        help="下载的文件类型，逗号分隔，可选值：pdf, markdown",
    )
    try:
        if not check_version():
            print(f"[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print(f"[WARNING] 检查 Gangtise skills 版本失败\n")

    args = parser.parse_args()
    keyword = args.keyword or ""
    securities = _parse_str_list(args.securities)
    category_list = _parse_str_list(args.category_list)
    start_date = args.start_date or None
    end_date = args.end_date or None
    limit = resolve_result_limit(args.limit, bool(args.download), "announcement")
    search_type = int(args.search_type or 1)
    rank_type = int(args.rank_type or 1)
    download = args.download or False
    output_dir = args.output_dir or None
    download_types = _parse_str_list(args.download_types)
    out = announcement_finder(
        keyword=keyword,
        securities=securities,
        start_date=start_date,
        end_date=end_date,
        category_list=category_list,
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
