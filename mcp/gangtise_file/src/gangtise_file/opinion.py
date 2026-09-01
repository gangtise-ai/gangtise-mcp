import os
import sys
from typing import List, Optional, Tuple
import datetime
import requests
from io import TextIOWrapper

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (  # noqa: E402
    DOWNLOAD_DEFAULT,
    DOWNLOAD_TYPE_DEFAULT,
    FILE_DEFAULT_LIMIT,
    OPINION_URL,
    RESEARCH_AREA_MAP,
    authorized_request,
    check_version,
    format_response,
    get_authorization_headers,
    get_authorization_token,
    get_headers_extra,
    match_best,
    remove_html_tags,
    resolve_result_limit,
)
from .get_file import download_files
from .security import batch_security_search
from .search_chief import SEARCH_TOP_DEFAULT, resolve_chief_token
from .search_institution import (
    CATEGORY_OPINION_INSTITUTION,
    USAGE_API_DOMESTIC_OPINION,
    USAGE_PARAM_BROKER_LIST,
    resolve_institution_tokens,
)

OPINION_LLM_TAG_MAP = {
    "强烈推荐": "strongRcmd",
    "业绩点评": "earningsReview",
    "头部券商": "topBroker",
    "新财富团队": "newFortune",
}

OPINION_SOURCE_MAP = {
    "实时": "realTime",
    "开放来源": "openSource",
}

OPINION_LLM_CODES = frozenset(OPINION_LLM_TAG_MAP.values())
OPINION_SOURCE_CODES = frozenset(OPINION_SOURCE_MAP.values())


def _format_time_range(start_date: str = None, end_date: str = None):
    start_timestamp = None
    end_timestamp = None
    if start_date:
        start_timestamp = int(datetime.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    if end_date:
        end_timestamp = int(
            (datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1)).timestamp() * 1000
        )
    return start_timestamp, end_timestamp


def _format_opinion_item(items: List[dict]) -> List[dict]:
    _results = []
    for row in items:
        content_list = row.get("contentList") if isinstance(row.get("contentList"), dict) else {}
        title = remove_html_tags(row.get("title") or content_list.get("title") or "")
        # 列表接口仅返回 brief（正文前 200 字）；兼容旧字段 contentList.content
        brief = remove_html_tags(
            row.get("brief")
            or content_list.get("brief")
            or ""
        )
        if not brief:
            legacy = remove_html_tags(content_list.get("content") or row.get("content") or "")
            brief = legacy[:200] if legacy else ""

        author = row.get("author") or {}
        chief_name = author.get("chiefName") or ""
        chief_id = author.get("chiefId") or ""
        chief_display = f"{chief_name}({chief_id})" if chief_id else chief_name

        research_areas = author.get("researchAreaList") or []
        ra_display = ", ".join([str(x) for x in research_areas if x]) if isinstance(research_areas, list) else ""

        broker_name = author.get("brokerName") or ""
        broker_id = author.get("brokerID") or ""
        broker_display = f"{broker_name}({broker_id})" if broker_id else broker_name

        security_items = row.get("securityList") or []
        stock_parts: List[str] = []
        for s in security_items:
            if not isinstance(s, dict):
                continue
            code = s.get("securityCode") or ""
            name = s.get("securityName") or ""
            if code:
                part = f"{name}({code})" if name else f"({code})"
                if part not in stock_parts:
                    stock_parts.append(part)
        stock_display = ", ".join(stock_parts)

        industry_items = row.get("industryList") or []
        ind_parts: List[str] = []
        for i in industry_items:
            if not isinstance(i, dict):
                continue
            ic = i.get("industryCode") or ""
            iname = i.get("industryName") or ""
            if ic or iname:
                part = f"{iname}({ic})" if ic else iname
                if part not in ind_parts:
                    ind_parts.append(part)
        industry_display = ", ".join(ind_parts)

        concept_items = row.get("conceptList") or []
        con_parts: List[str] = []
        for c in concept_items:
            if not isinstance(c, dict):
                continue
            cid = c.get("conceptId") or ""
            cname = c.get("conceptName") or ""
            if cid or cname:
                part = f"{cname}({cid})" if cid else cname
                if part not in con_parts:
                    con_parts.append(part)
        concept_display = ", ".join(con_parts)

        llm_tags = row.get("llmTagList") or []
        tag_display = ", ".join([str(x) for x in llm_tags if x]) if isinstance(llm_tags, list) else ""

        item = {
            "标题": title,
            "文件时间": (row.get("publishTime") or "") or "",
            "摘要": brief + ("..." if brief else ""),
            "首席": chief_display,
            "券商": broker_display,
            "研究方向": ra_display,
            "关联股票": stock_display,
            "关联行业": industry_display,
            "概念": concept_display,
            "标签": tag_display,
            "类型": "首席观点",
            "类型ID": str(row.get("chiefOpinionId") or ""),
        }
        _results.append(item)
    return _results


def _resolve_research_areas(industries: Optional[List[str]]) -> List[str]:
    """CLI 参数 industries → API researchAreaList（仅研究方向 ID，不含行业 industryList）。"""
    if not industries:
        return []
    id_set = {str(v) for v in RESEARCH_AREA_MAP.values()}
    results = []
    for raw in industries:
        if not raw:
            continue
        s = raw.strip()
        if s in id_set:
            if s not in results:
                results.append(s)
            continue
        m = match_best(s, list(RESEARCH_AREA_MAP.keys()))
        if m:
            sid = str(RESEARCH_AREA_MAP[m])
            if sid not in results:
                results.append(sid)
    return results


def _resolve_chiefs(
    chiefs: Optional[List[str]],
    headers: dict,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[str], List[str], Optional[str]]:
    """将 --chiefs 中的 chiefId 或关键词解析为 API chiefList。"""
    if not chiefs:
        return [], [], None

    resolved: List[str] = []
    notes: List[str] = []

    for raw in chiefs:
        token = raw.strip()
        if not token:
            continue
        chief_id, msg, is_candidates = resolve_chief_token(token, headers, top)
        if is_candidates:
            return [], [], msg
        if chief_id:
            if chief_id not in resolved:
                resolved.append(chief_id)
        elif msg:
            notes.append(f"首席「{token}」：{msg}")

    return resolved, notes, None


def _resolve_llm_tags(tags: Optional[List[str]]) -> List[str]:
    if not tags:
        return []
    out: List[str] = []
    for t in tags:
        if not t:
            continue
        s = t.strip()
        if s in OPINION_LLM_CODES:
            if s not in out:
                out.append(s)
            continue
        if s in OPINION_LLM_TAG_MAP:
            c = OPINION_LLM_TAG_MAP[s]
            if c not in out:
                out.append(c)
            continue
        if s not in out:
            out.append(s)
    return out


def _resolve_opinion_sources(source_types: Optional[List[str]]) -> List[str]:
    if not source_types:
        return []
    out: List[str] = []
    for t in source_types:
        if not t:
            continue
        s = t.strip()
        if s in OPINION_SOURCE_CODES:
            if s not in out:
                out.append(s)
            continue
        if s in OPINION_SOURCE_MAP:
            c = OPINION_SOURCE_MAP[s]
            if c not in out:
                out.append(c)
            continue
        if s not in out:
            out.append(s)
    return out


def _clean_keyword(
    keyword: str,
    securities=None,
    institutions=None,
    industries=None,
    chiefs=None,
    concepts=None,
    llm_tags=None,
    source_types=None,
):
    if not keyword:
        return ""
    keyword = (
        keyword.replace("[", "").replace("]", "")
        .replace("、", " ").replace("，", " ")
        .replace(", ", " ").replace(",", " ")
    )
    for items in [securities, institutions, industries, chiefs, concepts, llm_tags, source_types]:
        if items:
            for item in items:
                keyword = keyword.replace(item, "")
    return keyword.strip()


def _fetch_opinions(headers, payload_base, keyword, limit):
    max_page_size = 50
    all_results = []
    offset = 0
    remaining = limit

    while remaining > 0:
        page_size = min(remaining, max_page_size)
        data = {**payload_base, "from": offset, "size": page_size}
        if keyword:
            data["keyword"] = keyword
        response = authorized_request("POST", OPINION_URL, headers=headers, json=data)
        if response.status_code != 200:
            if all_results:
                return all_results, response.text.replace("\n", " ").replace("\r", " ").strip()
            return None, response.text.replace("\n", " ").replace("\r", " ").strip()
        result = response.json()

        if result.get("code") not in [200, "000000"] and result.get("status") is not True:
            return None, result.get("msg", "请求失败").replace("\n", " ").replace("\r", " ").strip()

        block = result.get("data") or {}
        rows = block.get("list") or []
        if not rows:
            break

        all_results.extend(_format_opinion_item(rows))

        if len(rows) < page_size:
            break

        offset += page_size
        remaining -= len(rows)

    return all_results, None


def opinion_finder(
    keyword: str = "",
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    institutions: Optional[List[str]] = None,
    industries: Optional[List[str]] = None,
    chiefs: Optional[List[str]] = None,
    concepts: Optional[List[str]] = None,
    llm_tags: Optional[List[str]] = None,
    source_types: Optional[List[str]] = None,
    rank_type: int = 1,
    limit: Optional[int] = None,
    download: bool = False,
    download_types: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    try:
        limit = resolve_result_limit(limit, download, "opinion")
        headers = get_authorization_headers()

        research_area_ids = _resolve_research_areas(industries) if industries else []
        broker_ids: List[str] = []
        if institutions:
            broker_ids, inst_notes, inst_candidates = resolve_institution_tokens(
                institutions,
                headers,
                [CATEGORY_OPINION_INSTITUTION],
                USAGE_PARAM_BROKER_LIST,
                USAGE_API_DOMESTIC_OPINION,
            )
            if inst_candidates:
                return format_response(
                    {"state": "error", "message": inst_candidates},
                    "opinion", output_dir=output_dir)
            if not broker_ids:
                msg = "机构解析失败"
                if inst_notes:
                    msg += "：" + "；".join(inst_notes)
                return format_response({"state": "error", "message": msg}, "opinion", output_dir=output_dir)
        llm_resolved = _resolve_llm_tags(llm_tags) if llm_tags else []
        source_resolved = _resolve_opinion_sources(source_types) if source_types else []

        securities_input = list(securities) if securities else []
        if securities_input:
            tokens = [str(s).strip() for s in securities_input if str(s).strip()]
            resolved = batch_security_search(
                tokens, category=["stock", "dr"], headers=headers, output_limit=1
            )
            if resolved.get("state") != "success":
                return format_response(
                    {"state": "error", "message": resolved.get("message") or "证券解析失败"},
                    "opinion", output_dir=output_dir)
            securities = resolved["codes"]
        else:
            securities = None

        chief_list: List[str] = []
        chief_notes: List[str] = []
        if chiefs:
            chief_list, chief_notes, chief_candidates = _resolve_chiefs(chiefs, headers)
            if chief_candidates:
                return format_response(
                    {"state": "error", "message": chief_candidates},
                    "opinion", output_dir=output_dir)
            if not chief_list:
                msg = "首席解析失败"
                if chief_notes:
                    msg += "：" + "；".join(chief_notes)
                return format_response({"state": "error", "message": msg}, "opinion", output_dir=output_dir)

        concept_list: List[str] = []
        if concepts:
            concept_list = [c.strip() for c in concepts if c and c.strip()]

        start_timestamp, end_timestamp = _format_time_range(start_date, end_date)

        keyword_str = _clean_keyword(
            keyword=keyword,
            securities=securities_input if securities_input else None,
            institutions=institutions,
            industries=industries,
            chiefs=chiefs,
            concepts=concepts,
            llm_tags=llm_tags,
            source_types=source_types,
        )

        payload_base: dict = {
            "rankType": rank_type,
        }
        if start_timestamp is not None:
            payload_base["startTime"] = start_timestamp
        if end_timestamp is not None:
            payload_base["endTime"] = end_timestamp
        if research_area_ids:
            payload_base["researchAreaList"] = research_area_ids
        if broker_ids:
            payload_base["brokerList"] = broker_ids
        if securities:
            payload_base["securityList"] = securities
        if chief_list:
            payload_base["chiefList"] = chief_list
        if concept_list:
            payload_base["conceptList"] = concept_list
        if llm_resolved:
            payload_base["llmTagList"] = llm_resolved
        if source_resolved:
            payload_base["sourceList"] = source_resolved

        all_results, err = _fetch_opinions(headers, payload_base, keyword_str, limit)
        if err and not all_results:
            return format_response({"state": "error", "message": err}, "opinion", output_dir=output_dir)
        part_error_message = ""
        if err and all_results:
            part_error_message = f"未完整获取全部结果，错误信息：{err}"
        if chief_notes:
            chief_msg = "；".join(chief_notes)
            part_error_message = (
                f"{part_error_message}；{chief_msg}" if part_error_message else chief_msg
            )

        if not all_results:
            return format_response(
                {"state": "error", "message": "未找到相关观点，建议修改查询条件", "data": []},
                "opinion", output_dir=output_dir)

        all_results = all_results[:limit]

        additional_message = None
        if download:
            dts = download_types or ["txt"]
            additional_message = download_files(
                all_results, "opinion", output_dir, download_types=dts
            ) + ("\n\n" + part_error_message if part_error_message else "")
        elif part_error_message:
            additional_message = part_error_message

        response_data = {
            "state": "success",
            "message": "已找到相关观点",
            "data": [{"data": all_results, "module": "opinion", "type": "files"}],
        }
        return format_response(response_data, "opinion", additional_message=additional_message or "", output_dir=output_dir)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "opinion", output_dir=output_dir)


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
        description="首席观点检索：按关键词、证券、券商、研究方向、首席、概念、标签等查询观点列表；可选 -d 通过 getDetail 下载正文。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-k", "--keyword", default="", help="搜索关键词，可为空")
    parser.add_argument("-sd", "--start-date", default="", help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("-ed", "--end-date", default="", help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "-l",
        "--limit",
        default=None,
        type=int,
        help="返回条数上限；不传时用检索默认，开启 -d 下载时默认 5",
    )
    parser.add_argument(
        "--rank-type",
        default=1,
        type=int,
        choices=[1, 2],
        help="排序：1 综合排序，2 时间倒序",
    )
    parser.add_argument(
        "--securities",
        default="",
        help="证券代码列表，逗号分隔，如 000001.SZ",
    )
    parser.add_argument(
        "--industries",
        default="",
        help="研究方向列表（映射为 researchAreaList），逗号分隔：宏观/策略/固收/金工/海外 等",
    )
    parser.add_argument(
        "--institutions",
        default="",
        help="发布机构（券商）列表，逗号分隔",
    )
    parser.add_argument(
        "--chiefs",
        default="",
        help="首席分析师列表，逗号分隔：支持 P 开头 ID 或姓名（模糊匹配，重名时请传 ID）",
    )
    parser.add_argument(
        "--concepts",
        default="",
        help="概念 ID 列表，逗号分隔",
    )
    parser.add_argument(
        "--llm-tags",
        default="",
        help="投研标签，逗号分隔：strongRcmd/earningsReview/topBroker/newFortune 或中文",
    )
    parser.add_argument(
        "--source-types",
        default="",
        help="来源，逗号分隔：realTime/openSource 或 实时/开放来源",
    )
    parser.add_argument(
        "-d",
        "--download",
        default=DOWNLOAD_DEFAULT,
        type=bool,
        help="检索后按观点 ID 调用 getDetail 下载正文原文（30 积分/条）",
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
        default=DOWNLOAD_TYPE_DEFAULT.get("opinion", "txt") or "txt",
        help="下载格式，逗号分隔：txt / md（正文相同，仅后缀不同）",
    )

    args = parser.parse_args()

    keyword = args.keyword or ""
    securities = _parse_str_list(args.securities)
    institutions = _parse_str_list(args.institutions)
    industries = _parse_str_list(args.industries)
    chiefs = _parse_str_list(args.chiefs)
    concepts = _parse_str_list(args.concepts)
    llm_tags = _parse_str_list(args.llm_tags)
    source_types = _parse_str_list(args.source_types)
    start_date = args.start_date or None
    end_date = args.end_date or None
    limit = resolve_result_limit(args.limit, bool(args.download), "opinion")
    rank_type = int(args.rank_type)
    download_types = [
        x.strip().lower()
        for x in str(args.download_types or "txt").replace("，", ",").split(",")
        if x.strip()
    ] or ["txt"]

    try:
        if not check_version():
            print(f"[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print(f"[WARNING] 检查 Gangtise skills 版本失败\n")

    out = opinion_finder(
        keyword=keyword,
        securities=securities,
        start_date=start_date,
        end_date=end_date,
        institutions=institutions,
        industries=industries,
        chiefs=chiefs,
        concepts=concepts,
        llm_tags=llm_tags,
        source_types=source_types,
        rank_type=rank_type,
        limit=limit,
        download=bool(args.download),
        download_types=download_types,
        output_dir=args.output_dir or None,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors='ignore')
    main()
