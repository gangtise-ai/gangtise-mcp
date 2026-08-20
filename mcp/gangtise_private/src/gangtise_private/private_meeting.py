import os
import sys
from datetime import datetime
from io import TextIOWrapper
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote
import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, FILE_DEFAULT_LIMIT, MY_CONFERENCE_DOWNLOAD_URL, MY_CONFERENCE_LIST_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, is_code_arg)

DEFAULT_LIST_LIMIT = FILE_DEFAULT_LIMIT.get("private_meeting", 100)
IDS_FILE_HINT = "可以删除行或保留需要的会议，再通过 --conference-file 参数下载内容"
POINTS_PER_LIST_ROW = 0.5
POINTS_PER_DOWNLOAD = 5

MEETING_CATEGORY_MAP = {
    "业绩会": "earningsCall",
    "策略会": "strategyMeeting",
    "基金路演": "fundRoadshow",
    "股东大会": "shareholdersMeeting",
    "并购会议": "maMeeting",
    "特别会议": "specialMeeting",
    "公司分析": "companyAnalysis",
    "行业分析": "industryAnalysis",
    "其他": "other",
}

CONTENT_TYPE_MAP = {
    "asr": "asr",
    "summary": "summary",
    "语音识别": "asr",
    "速记": "summary",
    "ai速记": "summary",
    "aisummary": "summary",
}


def _parse_str_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    items = [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]
    return items or None


def _normalize_datetime_field(raw: Optional[str], *, end_of_day: bool) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return s
        return s + (" 23:59:59" if end_of_day else " 00:00:00")
    return s


def _normalize_category_list(items: Optional[List[str]]) -> Optional[List[str]]:
    if not items:
        return None
    valid = set(MEETING_CATEGORY_MAP.values())
    out: List[str] = []
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        if s in MEETING_CATEGORY_MAP:
            v = MEETING_CATEGORY_MAP[s]
        elif s in valid:
            v = s
        else:
            v = MEETING_CATEGORY_MAP.get(s, s)
        if v and v not in out:
            out.append(v)
    return out or None


def _normalize_content_types(raw: Optional[str]) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    out: List[str] = []
    for part in str(raw).replace("，", ",").split(","):
        key = part.strip().lower()
        if key == "both" or key == "all":
            for v in ("asr", "summary"):
                if v not in out:
                    out.append(v)
            continue
        v = CONTENT_TYPE_MAP.get(key) or CONTENT_TYPE_MAP.get(part.strip())
        if v and v not in out:
            out.append(v)
    return out


def _check_api_ok(body: Dict[str, Any]) -> bool:
    code = body.get("code", "000000")
    return (code in (200, "000000") or code == "0") and body.get("status", True) is True


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _auth_headers(authorization: Optional[str] = None) -> Tuple[Optional[dict], Optional[str]]:
    auth = authorization or get_authorization_token()
    if not auth:
        return None, "未配置 gangtise 授权，无法调用 open 接口"
    headers = {**get_headers_extra(), "Authorization": auth}
    return headers, None


def _flatten_conference(row: dict) -> dict:
    inst = row.get("institution") or {}
    sec = row.get("security") or {}
    area = row.get("researchArea") or {}
    if not isinstance(inst, dict):
        inst = {}
    if not isinstance(sec, dict):
        sec = {}
    if not isinstance(area, dict):
        area = {}
    cat = row.get("category")
    if isinstance(cat, list):
        cat_str = ",".join(str(c) for c in cat if c)
    else:
        cat_str = str(cat or "")
    return {
        "conference_id": row.get("conferenceId"),
        "title": row.get("title"),
        "publish_time": row.get("publishTime"),
        "category": cat_str,
        "institution_id": inst.get("institutionId"),
        "institution_name": inst.get("institutionName"),
        "security_code": sec.get("securityCode"),
        "security_name": sec.get("securityName"),
        "research_area_id": area.get("researchAreaId"),
        "research_area_name": area.get("researchAreaName"),
        "guest": row.get("guest"),
    }


def _conferences_to_records(rows: List[dict]) -> List[dict]:
    return [_flatten_conference(r) for r in rows if isinstance(r, dict)]


def _fetch_conference_list(
    headers: dict,
    *,
    page_from: int = 0,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keyword: Optional[str] = None,
    security_list: Optional[List[str]] = None,
    institution_list: Optional[List[str]] = None,
    research_area_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    max_total: int = DEFAULT_LIST_LIMIT,
) -> Tuple[List[dict], int, Optional[str]]:
    cap = max(1, int(max_total))
    sz = min(50, max(1, int(page_size)))
    offset = max(0, int(page_from))
    aggregated: List[dict] = []
    total = 0
    part_err: Optional[str] = None

    st = _normalize_datetime_field(start_time, end_of_day=False)
    et = _normalize_datetime_field(end_time, end_of_day=True)

    while len(aggregated) < cap:
        chunk = min(sz, cap - len(aggregated))
        payload: Dict[str, Any] = {"from": offset, "size": chunk}
        if st:
            payload["startTime"] = st
        if et:
            payload["endTime"] = et
        if keyword:
            payload["keyword"] = str(keyword).strip()
        if security_list:
            payload["securityList"] = security_list
        if institution_list:
            payload["institutionList"] = institution_list
        if research_area_list:
            payload["researchAreaList"] = research_area_list
        if category_list:
            payload["categoryList"] = category_list

        try:
            r = authorized_request("POST", MY_CONFERENCE_LIST_URL, headers=headers, json=payload, timeout=300)
            if r.status_code != 200:
                if not aggregated:
                    return [], 0, r.text[:500]
                part_err = f"HTTP {r.status_code}"
                break
            body = r.json()
        except Exception as e:
            if not aggregated:
                return [], 0, str(e)
            part_err = str(e)
            break
        if not _check_api_ok(body):
            msg = _api_error_message(body, "会议列表请求失败")
            if not aggregated:
                return [], 0, msg
            part_err = msg
            break

        data_block = body.get("data") or body
        if isinstance(data_block, dict):
            total = int(data_block.get("total") or 0)
            raw_list = data_block.get("list") or []
        else:
            raw_list = []
        if not isinstance(raw_list, list):
            raw_list = []

        aggregated.extend(_conferences_to_records(raw_list))
        if len(raw_list) < chunk:
            break
        offset += len(raw_list)

    return aggregated, total, part_err


def _download_conference_content(
    headers: dict,
    conference_id: str,
    content_type: str,
    title: str = "",
    publish_time: str = "",
) -> Tuple[Optional[dict], Optional[str]]:
    cid = str(conference_id).strip()
    ctype = str(content_type).strip().lower()
    if not cid or ctype not in ("asr", "summary"):
        return None, "无效的 conferenceId 或 contentType"

    params = {"conferenceId": cid, "contentType": ctype}
    try:
        r = authorized_request("GET", 
            MY_CONFERENCE_DOWNLOAD_URL,
            headers=headers,
            params=params,
            timeout=300,
        )
    except Exception as e:
        return None, str(e)

    if r.status_code != 200:
        return None, f"下载 HTTP {r.status_code}: {r.text[:300]}"

    ctype_hdr = (r.headers.get("Content-Type") or "").lower()
    if "json" in ctype_hdr:
        try:
            body = r.json()
            if not _check_api_ok(body):
                return None, _api_error_message(body, "下载失败")
        except Exception:
            pass

    if ("text" in ctype_hdr or "json" in ctype_hdr or "xml" in ctype_hdr) and not (r.headers.get("Content-Disposition", None) and "filename" in r.headers["Content-Disposition"]):
        text = r.text or ""
        if not text.strip():
            return None, "下载内容为空"
        body_text = {"status": "failed", "message": text}
    else:
        size = len(r.content or b"")
        if size == 0:
            return None, "下载内容为空"
        if r.headers.get("Content-Disposition"):
            if len(r.headers["Content-Disposition"].lower().split("filename*=utf-8''")) > 1:
                filename = unquote(r.headers["Content-Disposition"].lower().split("filename*=utf-8''")[1])
            elif len(r.headers["Content-Disposition"].lower().split("filename=")) > 1:
                filename = unquote(r.headers["Content-Disposition"].lower().split("filename=")[1])
            else:
                filename = None
            if filename:
                body_text = {"status": "success", "file_bytes": r.content, "filename": filename}
            else:
                body_text = {"status": "success", "file_bytes": r.content}
        else:
            body_text = {"status": "failed", "message": "文件名无法获取"}

    label = "语音识别" if ctype == "asr" else "AI速记"
    item = {
        "标题": f"{title or cid}（{label}）",
        "文件时间": publish_time or "",
        "文件内容": body_text,
        "会议ID": cid,
        "内容类型": ctype,
        "类型": "我的会议",
        "类型ID": cid,
    }
    return item, None


def _load_conference_ids_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"会议文件不存在: {path}")
    df = pd.read_csv(path)
    for col in ("conference_id", "conferenceId", "类型ID"):
        if col in df.columns:
            return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]
    raise ValueError("会议文件须包含 conference_id 或 conferenceId 列")


def _list_response_parts(records: List[dict]) -> List[dict]:
    return [
        {
            "data": records,
            "module": "private_meeting_list",
            "type": "data",
        }
    ]


def _exact_match_conferences(keyword: str, records: List[dict]) -> List[dict]:
    kw = keyword.strip()
    return [
        r
        for r in records
        if str(r.get("title") or "").strip() == kw
        or str(r.get("conference_id") or "").strip() == kw
    ]


def _resolve_conferences_from_arg(
    raw: str,
    limit: int,
    **search_kwargs,
) -> Tuple[Optional[List[str]], Optional[str]]:
    if is_code_arg(raw):
        return _parse_str_list(raw) or [], None

    resolved: List[str] = []
    search_outputs: List[str] = []
    base_kwargs = {k: v for k, v in search_kwargs.items() if k != "keyword"}
    for token in _parse_str_list(raw) or []:
        out = private_meeting_search(keyword=token, limit=limit, append_file_hint=False, **base_kwargs)
        headers, err = _auth_headers()
        if err:
            return None, err
        records, _, _ = _fetch_conference_list(
            headers,
            keyword=token,
            max_total=limit,
            page_from=base_kwargs.get("page_from", 0),
            page_size=base_kwargs.get("page_size", 20),
            start_time=base_kwargs.get("start_time"),
            end_time=base_kwargs.get("end_time"),
            security_list=base_kwargs.get("security_list"),
            institution_list=base_kwargs.get("institution_list"),
            research_area_list=base_kwargs.get("research_area_list"),
            category_list=base_kwargs.get("category_list"),
        )
        flat = _conferences_to_records(records)
        if not flat:
            return None, f"未找到与「{token}」相关的会议"
        exact = _exact_match_conferences(token, flat)
        if len(exact) == 1:
            cid = str(exact[0].get("conference_id") or "").strip()
            if cid:
                resolved.append(cid)
            continue
        if len(exact) > 1:
            prefix = f"「{token}」存在 {len(exact)} 条完全匹配，请指定更精确的标题或 ID：\n\n"
        else:
            prefix = f"未找到与「{token}」完全匹配的会议，以下为候选结果：\n\n"
        search_outputs.append(prefix + out)

    if search_outputs:
        return None, "\n\n".join(search_outputs)
    return resolved, None


def private_meeting_search(
    keyword: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    security_list: Optional[List[str]] = None,
    institution_list: Optional[List[str]] = None,
    research_area_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output: Optional[str] = None,
    append_file_hint: bool = False,
    **kwargs,
):
    usage: dict = {}
    headers, err = _auth_headers()
    if err:
        return format_response({"state": "error", "message": err, "data": [], "usage": usage}, "private_meeting", output=output)

    records, total, part_err = _fetch_conference_list(
        headers,
        page_from=page_from,
        page_size=page_size,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        security_list=security_list,
        institution_list=institution_list,
        research_area_list=research_area_list,
        category_list=category_list,
        max_total=limit,
    )
    if not records:
        return format_response(
            {"state": "error", "message": part_err or "未找到会议记录", "data": [], "usage": usage},
            "private_meeting",
            output=output,
        )

    usage["my_conference_list"] = usage.get("my_conference_list", 0) + len(records) * POINTS_PER_LIST_ROW
    msg = f"已找到 {len(records)} 条会议记录"
    if total:
        msg += f"（接口 total={total}）"
    if append_file_hint:
        msg += f"；{IDS_FILE_HINT}"
    extra = f"\n未完整拉取：{part_err}" if part_err else ""
    return format_response(
        {"state": "success", "message": msg, "data": _list_response_parts(records), "usage": usage},
        "private_meeting",
        output=output,
        additional_message=extra.strip(),
    )


def private_meeting_get(
    conference_ids: List[str],
    content_types: List[str],
    meta_by_id: Optional[Dict[str, dict]] = None,
    output: Optional[str] = None,
    **kwargs,
):
    usage: dict = {}
    headers, err = _auth_headers()
    if err:
        return format_response({"state": "error", "message": err, "data": [], "usage": usage}, "private_meeting", output=output)

    ids = [str(x).strip() for x in conference_ids if str(x).strip()]
    ctypes = content_types or ["summary"]
    if not ids:
        return format_response(
            {
                "state": "error",
                "message": "未提供 conferenceId，请使用 --conference 或先运行 search 获取列表",
                "data": [],
                "usage": usage,
            },
            "private_meeting",
            output=output,
        )

    files: List[dict] = []
    errs: List[str] = []
    meta_by_id = meta_by_id or {}

    for cid in ids:
        meta = meta_by_id.get(cid) or {}
        title = meta.get("title") or ""
        publish_time = meta.get("publish_time") or ""
        for ctype in ctypes:
            item, derr = _download_conference_content(
                headers, cid, ctype, title=title, publish_time=publish_time
            )
            if derr:
                errs.append(f"{cid}/{ctype}: {derr}")
                continue
            if item:
                files.append(item)
                usage["my_conference_download"] = (
                    usage.get("my_conference_download", 0) + POINTS_PER_DOWNLOAD
                )

    if not files:
        return format_response(
            {
                "state": "error",
                "message": "；".join(errs) if errs else "未下载到会议内容",
                "data": [],
                "usage": usage,
            },
            "private_meeting",
            output=output,
        )

    parts = [{"data": files, "module": "private_meeting_content", "type": "files"}]
    msg = f"已下载 {len(files)} 份会议内容（{len(ids)} 个会议）"
    if errs:
        msg += f"；部分失败：{'；'.join(errs[:3])}"
    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "private_meeting",
        output=output,
    )


def private_meeting_finder(
    keyword: Optional[str] = None,
    conference_ids: Optional[str] = None,
    content_types: Optional[List[str]] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    security_list: Optional[List[str]] = None,
    institution_list: Optional[List[str]] = None,
    research_area_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output: Optional[str] = None,
):
    """检索电话会议列表，或在提供 conference_ids 时下载会议内容。"""
    common = dict(
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        security_list=security_list,
        institution_list=institution_list,
        research_area_list=research_area_list,
        category_list=category_list,
        limit=int(limit),
        page_from=page_from,
        page_size=page_size,
        output=output,
    )
    ids: List[str] = []
    if conference_ids and str(conference_ids).strip():
        resolved, err = _resolve_conferences_from_arg(
            str(conference_ids).strip(), int(limit), **common
        )
        if err:
            return err
        ids = resolved or []
    ctypes = content_types or ["summary"]
    if ids:
        return private_meeting_get(
            conference_ids=ids,
            content_types=ctypes,
            output=output,
        )
    return private_meeting_search(**common, append_file_hint=True)


def main():
    import argparse

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise private 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise private 版本失败\n")

    parser = argparse.ArgumentParser(
        description="我的会议：检索会议列表或按 conferenceId 下载 ASR/速记（open-vault my-conference）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["search", "get"],
        default="search",
        help="search=列会议；get=下载内容；提供 --conference/--conference-file 时自动为 get",
    )
    parser.add_argument("-k", "--keyword", default=None, help="会议标题关键词（search 模式）")
    parser.add_argument("-st", "--start-time", default=None, help="创建时间起（yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss）")
    parser.add_argument("-et", "--end-time", default=None, help="创建时间止")
    parser.add_argument("--securities", default=None, help="证券代码，逗号分隔")
    parser.add_argument("--institutions", default=None, help="牵头机构 ID，逗号分隔")
    parser.add_argument("--research-areas", default=None, help="研究方向 ID，逗号分隔")
    parser.add_argument(
        "--category-list",
        default=None,
        help="会议类别，逗号分隔（earningsCall 或中文如 业绩会/策略会 等）",
    )
    parser.add_argument(
        "--content-type",
        default="both",
        help="下载类型：asr（语音识别）、summary（AI速记）、both（get 模式）",
    )
    parser.add_argument(
        "--conference",
        default=None,
        help="会议 ID 或标题，逗号分隔；纯字母数字视为 ID 直接 get，否则 search，唯一完全匹配时自动 get",
    )
    parser.add_argument(
        "--conference-file",
        default=None,
        help="从 csv 读取 conference_id / conferenceId（get 模式）",
    )
    parser.add_argument("-l", "--limit", type=int, default=DEFAULT_LIST_LIMIT, help="search 模式列表条数上限")
    parser.add_argument("--page-from", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=20, help="单页条数，最大 50")
    parser.add_argument("-o", "--output", default=None, help="保存路径（需 GTS_SAVE_FILE=True 时用于内容）")

    args = parser.parse_args()
    content_types = _normalize_content_types(args.content_type)
    securities = _parse_str_list(args.securities)
    institutions = _parse_str_list(args.institutions)
    research_areas = _parse_str_list(args.research_areas)
    categories = _normalize_category_list(_parse_str_list(args.category_list))

    conference_arg = args.conference
    if args.conference_file:
        try:
            conference_arg = ",".join(_load_conference_ids_from_file(args.conference_file))
        except Exception as e:
            print(f"读取会议 ID 文件失败: {e}")
            sys.exit(1)

    print(
        private_meeting_finder(
            keyword=args.keyword,
            conference_ids=conference_arg,
            content_types=content_types,
            start_time=args.start_time,
            end_time=args.end_time,
            security_list=securities,
            institution_list=institutions,
            research_area_list=research_areas,
            category_list=categories,
            limit=int(args.limit),
            page_from=args.page_from,
            page_size=args.page_size,
            output=args.output,
        )
    )


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
