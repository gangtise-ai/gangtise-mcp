import os
import sys
from io import TextIOWrapper
from typing import List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .search_account import SEARCH_TOP_DEFAULT, resolve_account_token  # noqa: E402
from .utils import (DOWNLOAD_DEFAULT, DOWNLOAD_TYPE_DEFAULT, FILE_DEFAULT_LIMIT, FILE_DOWNLOAD_DEFAULT_LIMIT, INDUSTRIES_MAP, OFFICIAL_ACCOUNT_LIST_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, match_best, remove_html_tags, resolve_result_limit)
from .get_file import download_files
from .security import batch_security_search

ARTICLE_CATEGORY_LABEL = {
    "news": "新闻资讯",
    "law": "法律法规",
    "report": "报告类",
    "view": "个人观点",
    "data": "产业数据",
    "event": "日程活动",
    "meeting": "会议纪要",
    "notice": "通知",
    "recruit": "招聘",
    "investEdu": "投资知识科普",
    "brand": "品牌宣传",
    "notes": "个人随笔",
    "other": "其他",
}

ARTICLE_CATEGORY_CODE_MAP = {v: k for k, v in ARTICLE_CATEGORY_LABEL.items()}
ARTICLE_CATEGORY_CODES = frozenset(ARTICLE_CATEGORY_LABEL.keys())


def _normalize_api_time(value: Optional[str], end_of_day: bool) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    if len(text) <= 10:
        day = text[:10]
        return f"{day} 23:59:59" if end_of_day else f"{day} 00:00:00"
    return text


def _resolve_category_list(category_list: Optional[List[str]]) -> List[str]:
    if not category_list:
        return []
    resolved: List[str] = []
    for raw in category_list:
        item = str(raw).strip()
        if not item:
            continue
        if item in ARTICLE_CATEGORY_CODES:
            code = item
        elif item in ARTICLE_CATEGORY_CODE_MAP:
            code = ARTICLE_CATEGORY_CODE_MAP[item]
        else:
            code = item
        if code not in resolved:
            resolved.append(code)
    return resolved


def _resolve_industries(industries: Optional[List[str]]) -> List[str]:
    if not industries:
        return []
    all_industries = {}
    for value in INDUSTRIES_MAP.values():
        all_industries.update(value.copy())
    results: List[str] = []
    for industry in industries:
        hit = match_best(industry, list(all_industries.keys()))
        if not hit:
            continue
        industry_id = str(all_industries[hit])
        if industry_id not in results:
            results.append(industry_id)
    return results


def _join_named_items(items: object, id_key: str, name_key: str) -> str:
    parts: List[str] = []
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get(name_key) or "").strip()
        item_id = (item.get(id_key) or "").strip()
        if name and item_id:
            parts.append(f"{name}({item_id})")
        elif name:
            parts.append(name)
        elif item_id:
            parts.append(item_id)
    return "、".join(parts)


def _format_official_account_items(rows: List[dict]) -> List[dict]:
    results: List[dict] = []
    for row in rows:
        original_flag = row.get("originalFlag")
        if original_flag == 1:
            original_display = "原创"
        elif original_flag == 0:
            original_display = "非原创"
        else:
            original_display = ""

        category_raw = (row.get("articleCategory") or "").strip()
        category_display = ARTICLE_CATEGORY_LABEL.get(category_raw, category_raw)

        results.append(
            {
                "标题": remove_html_tags(row.get("title", "") or ""),
                "文件时间": (row.get("publishTime") or "") or "",
                "作者": (row.get("author") or "").strip(),
                "公众号": (row.get("accountName") or "").strip(),
                "公众号ID": (row.get("accountId") or "").strip(),
                "公众号简介": remove_html_tags(row.get("accountIntroduction", "") or ""),
                "所属证券": _join_named_items(row.get("securityList"), "securityCode", "securityName"),
                "所属板块": _join_named_items(row.get("industryList"), "industryId", "industryName"),
                "关联概念": _join_named_items(row.get("conceptList"), "conceptId", "conceptName"),
                "文章类型": category_display,
                "原创": original_display,
                "网络连接": (row.get("url") or "") or "",
                "摘要": remove_html_tags(row.get("summary", "") or ""),
                "类型": "公众号",
                "类型ID": str(row.get("articleId") or ""),
            }
        )
    return results


def _fetch_official_accounts(
    headers: dict,
    payload_base: dict,
    keyword: str,
    search_type: int,
    rank_type: int,
    limit: int,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    max_page_size = 50
    all_results: List[dict] = []
    offset = 0
    remaining = limit
    part_error: Optional[str] = None

    while remaining > 0:
        page_size = min(remaining, max_page_size)
        data = {
            **payload_base,
            "from": offset,
            "size": page_size,
            "rankType": rank_type,
        }
        if keyword:
            data["keyword"] = keyword
            data["searchType"] = search_type

        response = requests.post(OFFICIAL_ACCOUNT_LIST_URL, headers=headers, json=data, timeout=120)
        if response.status_code != 200:
            text = response.text.replace("\n", " ").replace("\r", " ").strip()
            if not all_results:
                return None, text
            part_error = text
            break

        result = response.json()
        if str(result.get("code", "")) != "000000" or result.get("status") is not True:
            msg = (result.get("msg") or "请求失败").replace("\n", " ").strip()
            if not all_results:
                return None, msg
            part_error = msg
            break

        block = result.get("data") or {}
        rows = block.get("list") or []
        if not rows:
            break

        all_results.extend(_format_official_account_items(rows))

        if len(rows) < page_size:
            break
        offset += page_size
        remaining -= len(rows)

    if part_error and all_results:
        return all_results, part_error
    return all_results, None


def _resolve_account_tokens(
    accounts: Optional[List[str]],
    headers: dict,
    account_category_list: Optional[List[str]] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[str], Optional[str]]:
    if not accounts:
        return [], None

    resolved: List[str] = []
    notes: List[str] = []

    for raw in accounts:
        token = raw.strip()
        if not token:
            continue
        account_id, msg, is_candidates = resolve_account_token(
            token, headers, category_list=account_category_list, top=top
        )
        if is_candidates:
            return [], msg
        if account_id:
            if account_id not in resolved:
                resolved.append(account_id)
        elif msg:
            notes.append(f"公众号「{token}」：{msg}")

    if not resolved:
        msg = "公众号解析失败"
        if notes:
            msg += "：" + "；".join(notes)
        return [], msg
    return resolved, None


def official_account_finder(
    keyword: str = "",
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    accounts: Optional[List[str]] = None,
    account_category_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    industry_list: Optional[List[str]] = None,
    search_type: int = 1,
    rank_type: int = 1,
    limit: Optional[int] = None,
    download: bool = False,
    download_types: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    try:
        limit = resolve_result_limit(limit, download, "official_account")
        headers = get_authorization_headers()

        resolved_account_ids: Optional[List[str]] = None
        if accounts:
            resolved_account_ids, account_err = _resolve_account_tokens(
                accounts, headers, account_category_list=account_category_list
            )
            if account_err:
                return format_response({"state": "error", "message": account_err}, "official_account")

        securities_input = list(securities) if securities else []
        if securities_input:
            tokens = [str(s).strip() for s in securities_input if str(s).strip()]
            resolved = batch_security_search(
                tokens, category=["stock", "dr"], headers=headers, output_limit=1
            )
            if resolved.get("state") != "success":
                return format_response(
                    {"state": "error", "message": resolved.get("message") or "证券解析失败"},
                    "official_account",
                )
            securities = resolved["codes"]
        else:
            securities = None

        industry_ids = _resolve_industries(industry_list)
        resolved_categories = _resolve_category_list(category_list)

        payload_base: dict = {}
        start_time = _normalize_api_time(start_date, end_of_day=False)
        end_time = _normalize_api_time(end_date, end_of_day=True)
        if start_time:
            payload_base["startTime"] = start_time
        if end_time:
            payload_base["endTime"] = end_time
        if resolved_account_ids:
            payload_base["accountIdList"] = resolved_account_ids
        if securities:
            payload_base["securityList"] = securities
        if resolved_categories:
            payload_base["categoryList"] = resolved_categories
        if industry_ids:
            payload_base["industryList"] = industry_ids

        keyword_str = (keyword or "").strip()
        part_error_message = ""

        all_results, err = _fetch_official_accounts(
            headers, payload_base, keyword_str, search_type, rank_type, limit
        )
        if err and not all_results:
            return format_response({"state": "error", "message": err}, "official_account")
        if err and all_results:
            part_error_message = f"未完整获取全部结果，错误信息：{err}"

        if not all_results and keyword_str and search_type != 2:
            all_results, err = _fetch_official_accounts(
                headers, payload_base, keyword_str, 2, rank_type, limit
            )
            if err and not all_results:
                return format_response({"state": "error", "message": err}, "official_account")
            if err and all_results:
                part_error_message = f"未完整获取全部结果，错误信息：{err}"

        if not all_results:
            return format_response(
                {
                    "state": "error",
                    "message": "未找到相关公众号资讯，建议修改查询条件",
                    "data": [],
                },
                "official_account",
            )

        all_results = all_results[:limit]

        additional_message = None
        if download:
            dts = download_types or ["txt"]
            additional_message = download_files(
                all_results, "official_account", output_dir, download_types=dts
            )
            if part_error_message:
                additional_message += "\n\n" + part_error_message

        response_data = {
            "state": "success",
            "message": "已找到相关公众号资讯",
            "data": [{"data": all_results, "module": "official_account", "type": "files"}],
        }
        return format_response(response_data, "official_account", additional_message=additional_message)
    except Exception as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "official_account",
        )


def _parse_str_list(raw: str) -> Optional[List[str]]:
    if not raw:
        return None
    items = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    return items or None


def _parse_download_types(raw: str) -> List[str]:
    if not raw or not str(raw).strip():
        return ["txt"]
    return [x.strip().lower() for x in raw.replace("，", ",").split(",") if x.strip()]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="公众号资讯检索（open-insight officialAccount/getList），支持下载 txt/HTML 正文。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-k", "--keyword", default="", help="检索关键词，需为具体词条而非白话问句")
    parser.add_argument(
        "-sd",
        "--start-date",
        default="",
        help="开始时间，YYYY-MM-DD 或 yyyy-MM-dd HH:mm:ss",
    )
    parser.add_argument(
        "-ed",
        "--end-date",
        default="",
        help="结束时间，YYYY-MM-DD 或 yyyy-MM-dd HH:mm:ss",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="返回条数上限；不传时检索默认见 FILE_DEFAULT_LIMIT，开启 -d 下载时默认 5",
    )
    parser.add_argument(
        "--securities",
        default="",
        help="证券列表，逗号分隔；可为证券名称、代码或拼音首字母",
    )
    parser.add_argument(
        "--accounts",
        default="",
        help="公众号 ID 或名称，逗号分隔；名称需完全匹配否则返回候选",
    )
    parser.add_argument(
        "--account-category-list",
        default="",
        help=(
            "解析 --accounts 名称时的公众号分类，逗号分隔："
            "listedCompany/broker/government/media 或 上市公司/券商团队/政府官方/媒体；"
            "不传则含未分类；传入后未分类公众号不会参与匹配"
        ),
    )
    parser.add_argument(
        "--category-list",
        default="",
        help="文章类型，逗号分隔。如 news,report 或 新闻资讯,报告类",
    )
    parser.add_argument(
        "--industries",
        default="",
        help="行业名称，逗号分隔；可选值见 get_industries.py",
    )
    parser.add_argument(
        "--search-type",
        type=int,
        default=1,
        help="搜索类型：1 标题搜索，2 全文搜索",
    )
    parser.add_argument(
        "--rank-type",
        type=int,
        default=1,
        help="排序方式：1 综合排序，2 时间倒序",
    )
    parser.add_argument(
        "-d",
        "--download",
        default=DOWNLOAD_DEFAULT,
        type=bool,
        help="检索后自动下载文章正文（txt/HTML）",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="下载保存目录，建议绝对路径",
    )
    parser.add_argument(
        "-dt",
        "--download-types",
        default=DOWNLOAD_TYPE_DEFAULT.get("official_account", "txt") or "txt",
        help="下载类型，逗号分隔：txt, html",
    )

    args = parser.parse_args()

    output_dir = args.output_dir or None
    if not args.download and output_dir:
        print("[WARNING] -od/--output-dir 仅在 -d 下载时有效，已忽略\n")
        output_dir = None

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise skills 版本失败\n")

    out = official_account_finder(
        keyword=args.keyword or "",
        securities=_parse_str_list(args.securities),
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        accounts=_parse_str_list(args.accounts),
        account_category_list=_parse_str_list(args.account_category_list),
        category_list=_parse_str_list(args.category_list),
        industry_list=_parse_str_list(args.industries),
        search_type=int(args.search_type or 1),
        rank_type=int(args.rank_type or 1),
        limit=resolve_result_limit(args.limit, bool(args.download), "official_account"),
        download=args.download,
        download_types=_parse_download_types(args.download_types),
        output_dir=output_dir,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
