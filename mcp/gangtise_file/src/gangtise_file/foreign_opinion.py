import os
import sys
from typing import List, Optional, Tuple

import datetime
import requests

from io import TextIOWrapper

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (DOWNLOAD_DEFAULT, DOWNLOAD_TYPE_DEFAULT, FILE_DEFAULT_LIMIT, FILE_DOWNLOAD_DEFAULT_LIMIT, FOREIGN_OPINION_URL, INDEPENDENT_OPINION_LIST_URL, INDUSTRIES_MAP, REGIONS_MAP, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, match_best, remove_html_tags, resolve_result_limit)
from .get_file import download_files
from .security import batch_security_search
from .search_institution import (
    CATEGORY_FOREIGN_INSTITUTION,
    CATEGORY_FOREIGN_OPINION_INSTITUTION,
    USAGE_API_FOREIGN_OPINION,
    USAGE_PARAM_BROKER_LIST,
    resolve_institution_tokens,
)


def _normalize_api_time(s: Optional[str], end_of_day: bool) -> Optional[str]:
    """接口 startTime/endTime：yyyy-MM-dd HH:mm:ss，兼容仅传 yyyy-MM-dd。"""
    if not s or not str(s).strip():
        return None
    t = str(s).strip()
    if len(t) <= 10:
        d = t[:10]
        return f"{d} 23:59:59" if end_of_day else f"{d} 00:00:00"
    return t


def _security_display_list(securities: object) -> Tuple[str, List[str]]:
    lines: List[str] = []
    if not isinstance(securities, list):
        return "", lines
    for s in securities:
        if not isinstance(s, dict):
            continue
        code = (s.get("securityCode") or "").strip()
        name = (s.get("securityName") or "").strip()
        rating = (s.get("rating") or "") or ""
        rc = (s.get("ratingChange") or "") or ""
        tp = s.get("targetPrice")
        cur = (s.get("currency") or "").strip()
        extra = []
        if rating:
            extra.append(f"评级:{rating}")
        if rc:
            extra.append(f"变动:{rc}")
        if tp is not None and str(tp) != "":
            extra.append(f"目标价:{tp}{cur}")
        seg = f"{name}({code})" if name and code else (code or name)
        if extra:
            seg += " " + " ".join(extra)
        if seg:
            lines.append(seg)
    return "、".join(lines), lines


def _industry_display_list(industries: object) -> str:
    parts: List[str] = []
    if not isinstance(industries, list):
        return ""
    for ind in industries:
        if not isinstance(ind, dict):
            continue
        name = (ind.get("industryName") or "").strip()
        iid = (ind.get("industryId") or "").strip()
        rating = (ind.get("rating") or "") or ""
        rc = (ind.get("ratingChange") or "") or ""
        seg = f"{name}({iid})" if name and iid else (name or iid)
        if rating or rc:
            seg += f" 评级:{rating or '-'} 变动:{rc or '-'}"
        if seg:
            parts.append(seg)
    return "、".join(parts)


def _format_foreign_institution_items(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for it in rows:
        pub = it.get("publisher") if isinstance(it.get("publisher"), dict) else {}
        broker_name = (pub.get("brokerName") or "").strip()
        author = (pub.get("author") or "").strip()
        region_obj = it.get("region") if isinstance(it.get("region"), dict) else {}
        rn = (region_obj.get("regionName") or "").strip()
        rc = (region_obj.get("regionCode") or "").strip()
        region_display = f"{rn}({rc})" if rn and rc else (rn or rc)
        sec_line, _ = _security_display_list(it.get("securityList"))
        ind_line = _industry_display_list(it.get("industryList"))
        title_tr = remove_html_tags(it.get("titleTranslate", "") or "")
        content_tr = remove_html_tags(it.get("contentTranslate", "") or "")
        out.append(
            {
                "标题": remove_html_tags(it.get("title", "") or ""),
                "中文标题": title_tr,
                "文件时间": (it.get("publishTime") or "") or "",
                "作者": author,
                "来源机构": broker_name,
                "所属区域": region_display,
                "所属证券": sec_line,
                "所属板块": ind_line,
                "摘要": remove_html_tags(it.get("content", "") or ""),
                "中文摘要": content_tr,
                "类型": "外资机构观点",
                "类型ID": str(it.get("foreignOpinionId", "") or ""),
            }
        )
    return out


def _format_independent_items(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for it in rows:
        an = it.get("analyst") if isinstance(it.get("analyst"), dict) else {}
        analyst_name = (an.get("analystName") or "").strip()
        analyst_id = (an.get("analystId") or "").strip()
        analyst_display = f"{analyst_name}({analyst_id})" if analyst_name and analyst_id else (analyst_name or analyst_id)
        sec_line, _ = _security_display_list(it.get("securityList"))
        ind_line = _industry_display_list(it.get("industryList"))
        title_tr = remove_html_tags(it.get("titleTranslate", "") or "")
        brief_tr = remove_html_tags(it.get("briefTranslate", "") or "")
        out.append(
            {
                "标题": remove_html_tags(it.get("title", "") or ""),
                "中文标题": title_tr,
                "文件时间": (it.get("publishTime") or "") or "",
                "分析师": analyst_display,
                "所属证券": sec_line,
                "所属板块": ind_line,
                "摘要": remove_html_tags(it.get("brief", "") or ""),
                "中文摘要": brief_tr,
                "类型": "外资独立观点",
                "类型ID": str(it.get("independentOpinionId", "") or ""),
            }
        )
    return out


def _resolve_industries(industries: List[str]) -> List[str]:
    if not industries:
        return []
    all_industries = {}
    for _k, value in INDUSTRIES_MAP.items():
        all_industries.update(value.copy())
    results: List[str] = []
    for industry in industries:
        result = match_best(industry, list(all_industries.keys()))
        if not result:
            continue
        rid = str(all_industries[result])
        if rid not in results:
            results.append(rid)
    return results


def _resolve_regions(regions: List[str]) -> List[str]:
    if not regions:
        return []
    valid_ids = set(REGIONS_MAP.values())
    results: List[str] = []
    for raw in regions:
        r = str(raw).strip()
        if not r:
            continue
        if r in valid_ids:
            if r not in results:
                results.append(r)
            continue
        result = match_best(r, list(REGIONS_MAP.keys()))
        if result and REGIONS_MAP.get(result) not in results:
            results.append(str(REGIONS_MAP[result]))
    return results


def _clean_keyword(keyword: str, securities=None, institutions=None, industries=None) -> str:
    if not keyword:
        return ""
    keyword = (
        keyword.replace("[", "").replace("]", "")
        .replace("、", " ").replace("，", " ")
        .replace(", ", " ").replace(",", " ")
    )
    for token in ("外资机构观点", "外资独立观点", "独立观点", "机构观点"):
        keyword = keyword.replace(token, "")
    for items in (securities, institutions, industries):
        if items:
            for item in items:
                keyword = keyword.replace(str(item), "")
    return keyword.strip()


def _fetch_opinion_pages(
    url: str,
    headers: dict,
    payload_base: dict,
    keyword: str,
    rank_type: int,
    limit: int,
    formatter,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    max_page_size = 50
    all_rows: List[dict] = []
    offset = 0
    remaining = limit
    part_err: Optional[str] = None

    while remaining > 0:
        page_size = min(remaining, max_page_size)
        data = {**payload_base, "from": offset, "size": page_size, "rankType": rank_type}
        if keyword:
            data["keyword"] = keyword

        response = authorized_request("POST", url, headers=headers, json=data, timeout=120)
        if response.status_code != 200:
            text = response.text.replace("\n", " ").replace("\r", " ").strip()
            if not all_rows:
                return None, text
            part_err = text
            break

        result = response.json()
        if str(result.get("code", "")) != "000000" or result.get("status") is not True:
            msg = (result.get("msg") or "请求失败").replace("\n", " ").strip()
            if not all_rows:
                return None, msg
            part_err = msg
            break

        block = result.get("data") or {}
        rows = block.get("list") or []
        if not rows:
            break

        all_rows.extend(formatter(rows))

        if len(rows) < page_size:
            break
        offset += page_size
        remaining -= len(rows)

    if part_err and all_rows:
        return all_rows, part_err
    return all_rows, None


def opinion_finder(
    source: str = "institution",
    keyword: str = "",
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    institutions: Optional[List[str]] = None,
    industries: Optional[List[str]] = None,
    region_list: Optional[List[str]] = None,
    rating_list: Optional[List[str]] = None,
    rating_change_list: Optional[List[str]] = None,
    rank_type: int = 1,
    limit: Optional[int] = None,
    download: bool = False,
    download_types: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    """
    source: institution — 外资机构观点（foreign-opinion/getList）；
            independent — 外资独立观点（independent-opinion/getList），可配合 download 下载 HTML。
    """
    source = (source or "institution").strip().lower()
    if source not in ("institution", "independent"):
        return format_response(
            {"state": "error", "message": "source 仅支持 institution（外资机构观点）或 independent（外资独立观点）", "data": []},
            "foreign_opinion", output_dir=output_dir)

    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": []},
            "foreign_opinion", output_dir=output_dir)

    try:
        limit = resolve_result_limit(limit, download, "foreign_opinion")
        headers = get_authorization_headers()
        
        industry_ids = _resolve_industries(industries) if industries else []
        org_ids: List[str] = []
        if institutions and source == "institution":
            org_ids, inst_notes, inst_candidates = resolve_institution_tokens(
                institutions,
                headers,
                [CATEGORY_FOREIGN_OPINION_INSTITUTION, CATEGORY_FOREIGN_INSTITUTION],
                USAGE_PARAM_BROKER_LIST,
                USAGE_API_FOREIGN_OPINION,
            )
            if inst_candidates:
                return format_response(
                    {"state": "error", "message": inst_candidates, "data": []},
                    "foreign_opinion", output_dir=output_dir)
            if not org_ids:
                msg = "机构解析失败"
                if inst_notes:
                    msg += "：" + "；".join(inst_notes)
                return format_response(
                    {"state": "error", "message": msg, "data": []},
                    "foreign_opinion", output_dir=output_dir)
        region_ids = _resolve_regions(region_list) if region_list and source == "institution" else []

        securities_input = list(securities) if securities else []
        if securities_input:
            tokens = [str(s).strip() for s in securities_input if str(s).strip()]
            resolved = batch_security_search(
                tokens, category=["stock", "dr"], headers=headers, output_limit=1
            )
            if resolved.get("state") != "success":
                return format_response(
                    {"state": "error", "message": resolved.get("message") or "证券解析失败"},
                    "foreign_opinion", output_dir=output_dir)
            securities_resolved = resolved["codes"]
        else:
            securities_resolved = None

        if securities_resolved and industries:
            industry_ids = []

        st = _normalize_api_time(start_date, end_of_day=False)
        et = _normalize_api_time(end_date, end_of_day=True)

        payload_base: dict = {}
        if st:
            payload_base["startTime"] = st
        if et:
            payload_base["endTime"] = et
        if securities_resolved:
            payload_base["securityList"] = securities_resolved
        if industry_ids:
            payload_base["industryList"] = industry_ids
        if rating_list:
            payload_base["ratingList"] = rating_list
        if rating_change_list:
            payload_base["ratingChangeList"] = rating_change_list

        if source == "institution":
            if org_ids:
                payload_base["brokerList"] = org_ids
            if region_ids:
                payload_base["regionList"] = region_ids
            url = FOREIGN_OPINION_URL
            formatter = _format_foreign_institution_items
            label = "外资机构观点"
        else:
            url = INDEPENDENT_OPINION_LIST_URL
            formatter = _format_independent_items
            label = "外资独立观点"

        keyword_str = _clean_keyword(
            keyword,
            securities_input if securities_input else None,
            institutions if source == "institution" else None,
            industries,
        )

        all_results, err = _fetch_opinion_pages(url, headers, payload_base, keyword_str, rank_type, limit, formatter)
        part_error_message = ""
        if err and not all_results:
            return format_response({"state": "error", "message": err}, "foreign_opinion", output_dir=output_dir)
        if err and all_results:
            part_error_message = f"未完整获取全部结果，错误信息：{err}"

        if not all_results:
            return format_response(
                {"state": "error", "message": f"未找到相关{label}，建议修改查询条件", "data": []},
                "foreign_opinion", output_dir=output_dir)

        all_results = all_results[:limit]

        additional_message = None
        if download:
            if source != "independent":
                additional_message = "当前来源为外资机构观点，接口不提供原文下载；-d 仅对外资独立观点有效。"
            else:
                dts = download_types or ["html"]
                additional_message = download_files(
                    all_results, "foreign_opinion", output_dir, download_types=dts
                ) + ("\n\n" + part_error_message if part_error_message else "")

        if part_error_message and not (download and source == "independent"):
            additional_message = (additional_message + "\n\n" if additional_message else "") + part_error_message

        response_data = {
            "state": "success",
            "message": f"已找到相关{label}",
            "data": [{"data": all_results, "module": "foreign_opinion", "type": "files"}],
        }
        return format_response(response_data, "foreign_opinion", additional_message=additional_message, output_dir=output_dir)
    except Exception as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "foreign_opinion", output_dir=output_dir)


def _parse_str_list(raw: str) -> Optional[List[str]]:
    if not raw:
        return None
    items = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    return items or None


def _parse_download_types(raw: str) -> List[str]:
    """独立观点 HTML：html / zh 等，逗号分隔。"""
    if not raw or not str(raw).strip():
        return ["html"]
    return [x.strip().lower() for x in raw.replace("，", ",").split(",") if x.strip()]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="外资机构观点 / 外资独立观点 检索（open-insight）；独立观点支持下载 HTML。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=["institution", "independent"],
        default="institution",
        help="institution=外资机构观点；independent=外资独立观点（可 -d 下载）",
    )
    parser.add_argument("-k", "--keyword", default="", help="关键词，可为空")
    parser.add_argument("-sd", "--start-date", default="", help="开始日期 YYYY-MM-DD 或 yyyy-MM-dd HH:mm:ss")
    parser.add_argument("-ed", "--end-date", default="", help="结束日期 YYYY-MM-DD 或 yyyy-MM-dd HH:mm:ss")
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="返回条数上限；不传时检索默认见 FILE_DEFAULT_LIMIT，开启 -d 下载时默认 5",
    )
    parser.add_argument("--securities", default="", help="证券代码列表，逗号分隔，境外如 APP.O")
    parser.add_argument(
        "--institutions",
        default="",
        help="券商列表（逗号分隔，仅 institution 源会提交 brokerList）",
    )
    parser.add_argument("--industries", default="", help="行业关键词列表，逗号分隔")
    parser.add_argument(
        "--region-list",
        default="",
        help="区域列表（逗号分隔；仅 institution 源），可为区域代码或中文名",
    )
    parser.add_argument(
        "--rating-list",
        default="",
        help="评级：buy,overweight,neutral,underweight,sell",
    )
    parser.add_argument(
        "--rating-change-list",
        default="",
        help="评级变动：upgrade,maintain,downgrade,initiate",
    )
    parser.add_argument("--rank-type", type=int, default=1, help="1 综合排序 2 时间倒序")
    parser.add_argument(
        "-d",
        "--download",
        default=DOWNLOAD_DEFAULT,
        help="检索后下载文件（仅 independent 有效，外资独立观点 HTML）",
    )
    parser.add_argument("-od", "--output-dir", default=None, help="结果与下载文件保存目录路径，建议使用绝对路径")
    parser.add_argument(
        "-dt",
        "--download-types",
        default=DOWNLOAD_TYPE_DEFAULT.get("foreign_opinion", "html") or "html",
        help="独立观点下载类型，逗号分隔：html（原文）, html_zh（中文翻译）；与 get_file 一致",
    )

    args = parser.parse_args()

    keyword = args.keyword or ""
    securities = _parse_str_list(args.securities)
    institutions = _parse_str_list(args.institutions)
    industries = _parse_str_list(args.industries)
    region_list = _parse_str_list(args.region_list)
    rating_list = _parse_str_list(args.rating_list)
    rating_change_list = _parse_str_list(args.rating_change_list)
    start_date = args.start_date or None
    end_date = args.end_date or None
    limit = resolve_result_limit(args.limit, bool(args.download), "foreign_opinion")
    rank_type = int(args.rank_type or 1)
    output_dir = args.output_dir or None
    download_types = _parse_download_types(args.download_types)

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise skills 版本失败\n")

    out = opinion_finder(
        source=args.source,
        keyword=keyword,
        securities=securities,
        start_date=start_date,
        end_date=end_date,
        institutions=institutions,
        industries=industries,
        region_list=region_list,
        rating_list=rating_list,
        rating_change_list=rating_change_list,
        rank_type=rank_type,
        limit=limit,
        download=args.download,
        download_types=download_types,
        output_dir=output_dir,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
