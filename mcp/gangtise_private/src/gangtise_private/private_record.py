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

from .utils import (FILE_DEFAULT_LIMIT, RECORD_DOWNLOAD_URL, RECORD_LIST_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, is_code_arg)

DEFAULT_LIST_LIMIT = FILE_DEFAULT_LIMIT.get("private_record", 100)
IDS_FILE_HINT = "可以删除行或保留需要的录音，再通过 --record-file 参数下载内容"

RECORD_CATEGORY_MAP = {
    "上传文件": "upload",
    "上传": "upload",
    "导入链接": "link",
    "链接": "link",
    "手机录音": "mobile",
    "手机": "mobile",
    "录音卡": "gtNote",
    "gtnote": "gtNote",
    "pc录音": "pc",
    "PC录音": "pc",
    "pc": "pc",
    "与我分享": "share",
    "分享": "share",
}

RECORD_CATEGORY_VALID = {"upload", "link", "mobile", "gtNote", "pc", "share"}

SPACE_TYPE_MAP = {
    "我的速记": 1,
    "租户速记": 2,
    "1": 1,
    "2": 2,
}

CONTENT_TYPE_MAP = {
    "original": "original",
    "asr": "asr",
    "summary": "summary",
    "原始": "original",
    "原始文件": "original",
    "语音识别": "asr",
    "速记": "summary",
    "ai速记": "summary",
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
    out: List[str] = []
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        key = s.lower()
        if s in RECORD_CATEGORY_MAP:
            v = RECORD_CATEGORY_MAP[s]
        elif key in RECORD_CATEGORY_VALID:
            v = key if key != "gtnote" else "gtNote"
        elif key == "gtnote":
            v = "gtNote"
        else:
            v = RECORD_CATEGORY_MAP.get(s, s)
        if v in RECORD_CATEGORY_VALID and v not in out:
            out.append(v)
    return out or None


def _normalize_space_type_list(items: Optional[List[str]]) -> Optional[List[int]]:
    if not items:
        return None
    out: List[int] = []
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        if s in SPACE_TYPE_MAP:
            v = SPACE_TYPE_MAP[s]
        else:
            try:
                v = int(s)
            except ValueError:
                continue
        if v in (1, 2) and v not in out:
            out.append(v)
    return out or None


def _normalize_content_types(raw: Optional[str]) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    out: List[str] = []
    for part in str(raw).replace("，", ",").split(","):
        key = part.strip().lower()
        if key == "all":
            for v in ("original", "asr", "summary"):
                if v not in out:
                    out.append(v)
            continue
        if key == "both":
            for v in ("asr", "summary"):
                if v not in out:
                    out.append(v)
            continue
        v = CONTENT_TYPE_MAP.get(key) or CONTENT_TYPE_MAP.get(part.strip())
        if v in ("original", "asr", "summary") and v not in out:
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


def _flatten_record(row: dict) -> dict:
    st = row.get("spaceType")
    return {
        "record_id": row.get("recordId"),
        "title": row.get("title"),
        "create_time": row.get("createTime"),
        "category": row.get("category"),
        "record_duration": row.get("recordDuration"),
        "record_size": row.get("recordSize"),
        "url": row.get("url"),
        "space_type": st,
        "uploader": row.get("uploader"),
    }


def _fetch_record_list(
    headers: dict,
    *,
    page_from: int = 0,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keyword: Optional[str] = None,
    category_list: Optional[List[str]] = None,
    space_type_list: Optional[List[int]] = None,
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
        if category_list:
            payload["categoryList"] = category_list
        if space_type_list:
            payload["spaceTypeList"] = space_type_list

        try:
            r = authorized_request("POST", RECORD_LIST_URL, headers=headers, json=payload, timeout=300)
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
            msg = _api_error_message(body, "录音列表请求失败")
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

        for row in raw_list:
            if isinstance(row, dict):
                aggregated.append(_flatten_record(row))
        if len(raw_list) < chunk:
            break
        offset += len(raw_list)
    return aggregated, total, part_err


def _content_type_label(ctype: str) -> str:
    return {"original": "原始文件", "asr": "语音识别", "summary": "AI速记"}.get(ctype, ctype)


def _download_record_content(
    headers: dict,
    record_id: str,
    content_type: str,
    title: str = "",
    create_time: str = "",
    category: str = "",
) -> Tuple[Optional[dict], Optional[str]]:
    rid = str(record_id).strip()
    ctype = str(content_type).strip().lower()
    if not rid or ctype not in ("original", "asr", "summary"):
        return None, "无效的 recordId 或 contentType"

    if ctype == "original" and str(category).strip().lower() == "share":
        return None, "与我分享类型的录音无法下载原始文件"

    params = {"recordId": rid, "contentType": ctype}
    try:
        r = authorized_request("GET", RECORD_DOWNLOAD_URL, headers=headers, params=params, timeout=300)
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

    item = {
        "标题": f"{title or rid}（{_content_type_label(ctype)}）",
        "文件时间": create_time or "",
        "文件内容": body_text,
        "录音ID": rid,
        "内容类型": ctype,
        "类型": "录音速记",
        "类型ID": rid,
    }
    return item, None


def _load_record_ids_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"录音文件不存在: {path}")
    df = pd.read_csv(path)
    for col in ("record_id", "recordId", "类型ID"):
        if col in df.columns:
            return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]
    raise ValueError("文件须包含 record_id 或 recordId 列")


def _list_response_parts(records: List[dict]) -> List[dict]:
    return [{"data": records, "module": "private_record_list", "type": "data"}]


def _exact_match_records(keyword: str, records: List[dict]) -> List[dict]:
    kw = keyword.strip()
    return [
        r
        for r in records
        if str(r.get("title") or "").strip() == kw
        or str(r.get("record_id") or "").strip() == kw
    ]


def _resolve_records_from_arg(
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
        out = private_record_search(keyword=token, limit=limit, append_file_hint=False, **base_kwargs)
        headers, err = _auth_headers()
        if err:
            return None, err
        records, _, _ = _fetch_record_list(
            headers,
            keyword=token,
            max_total=limit,
            page_from=base_kwargs.get("page_from", 0),
            page_size=base_kwargs.get("page_size", 20),
            start_time=base_kwargs.get("start_time"),
            end_time=base_kwargs.get("end_time"),
            category_list=base_kwargs.get("category_list"),
            space_type_list=base_kwargs.get("space_type_list"),
        )
        if not records:
            return None, f"未找到与「{token}」相关的录音"
        exact = _exact_match_records(token, records)
        if len(exact) == 1:
            rid = str(exact[0].get("record_id") or "").strip()
            if rid:
                resolved.append(rid)
            continue
        if len(exact) > 1:
            prefix = f"「{token}」存在 {len(exact)} 条完全匹配，请指定更精确的标题或 ID：\n\n"
        else:
            prefix = f"未找到与「{token}」完全匹配的录音，以下为候选结果：\n\n"
        search_outputs.append(prefix + out)

    if search_outputs:
        return None, "\n\n".join(search_outputs)
    return resolved, None


def private_record_search(
    keyword: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    category_list: Optional[List[str]] = None,
    space_type_list: Optional[List[int]] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output_dir: Optional[str] = None,
    append_file_hint: bool = False,
    **kwargs,
):
    usage: dict = {}
    headers, err = _auth_headers()
    if err:
        return format_response({"state": "error", "message": err, "data": [], "usage": usage}, "private_record", output_dir=output_dir)

    records, total, part_err = _fetch_record_list(
        headers,
        page_from=page_from,
        page_size=page_size,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        category_list=category_list,
        space_type_list=space_type_list,
        max_total=limit,
    )
    if not records:
        return format_response(
            {"state": "error", "message": part_err or "未找到录音记录", "data": [], "usage": usage},
            "private_record",
            output_dir=output_dir,
        )

    msg = f"已找到 {len(records)} 条录音"
    if total:
        msg += f"（total={total}）"
    if append_file_hint:
        msg += f"；{IDS_FILE_HINT}"
    extra = f"\n未完整拉取：{part_err}" if part_err else ""
    return format_response(
        {"state": "success", "message": msg, "data": _list_response_parts(records), "usage": usage},
        "private_record",
        output_dir=output_dir,
        additional_message=extra.strip(),
    )


def private_record_get(
    record_ids: List[str],
    content_types: List[str],
    meta_by_id: Optional[Dict[str, dict]] = None,
    output_dir: Optional[str] = None,
    **kwargs,
):
    usage: dict = {}
    headers, err = _auth_headers()
    if err:
        return format_response({"state": "error", "message": err, "data": [], "usage": usage}, "private_record", output_dir=output_dir)

    ids = [str(x).strip() for x in record_ids if str(x).strip()]
    ctypes = content_types or ["summary"]
    if not ids:
        return format_response(
            {
                "state": "error",
                "message": "未提供 recordId，请使用 --record 或先运行 search 获取列表",
                "data": [],
                "usage": usage,
            },
            "private_record",
            output_dir=output_dir,
        )

    files: List[dict] = []
    errs: List[str] = []
    meta_by_id = meta_by_id or {}

    for rid in ids:
        meta = meta_by_id.get(rid) or {}
        title = str(meta.get("title") or "")
        create_time = str(meta.get("create_time") or "")
        category = str(meta.get("category") or "")
        for ctype in ctypes:
            item, derr = _download_record_content(
                headers,
                rid,
                ctype,
                title=title,
                create_time=create_time,
                category=category,
            )
            if derr:
                errs.append(f"{rid}/{ctype}: {derr}")
                continue
            if item:
                files.append(item)

    if not files:
        return format_response(
            {
                "state": "error",
                "message": "；".join(errs) if errs else "未下载到录音内容",
                "data": [],
                "usage": usage,
            },
            "private_record",
            output_dir=output_dir,
        )

    parts = [{"data": files, "module": "private_record_content", "type": "files"}]
    msg = f"已下载 {len(files)} 份录音内容（{len(ids)} 条录音）"
    if errs:
        msg += f"；部分失败：{'；'.join(errs[:3])}"
    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "private_record",
        output_dir=output_dir,
    )


def private_record_finder(
    keyword: Optional[str] = None,
    record_ids: Optional[str] = None,
    content_types: Optional[List[str]] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    category_list: Optional[List[str]] = None,
    space_type_list: Optional[List[int]] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output_dir: Optional[str] = None,
):
    """检索录音列表，或在提供 record_ids 时下载录音内容。"""
    common = dict(
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        category_list=category_list,
        space_type_list=space_type_list,
        limit=int(limit),
        page_from=page_from,
        page_size=page_size,
        output_dir=output_dir,
    )
    ids: List[str] = []
    if record_ids and str(record_ids).strip():
        resolved, err = _resolve_records_from_arg(str(record_ids).strip(), int(limit), **common)
        if err:
            return err
        ids = resolved or []
    ctypes = content_types or ["summary"]
    if ids:
        return private_record_get(record_ids=ids, content_types=ctypes, output_dir=output_dir)
    return private_record_search(**common, append_file_hint=True)


def main():
    import argparse

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise private 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise private 版本失败\n")

    parser = argparse.ArgumentParser(
        description="录音速记：检索录音列表或按 recordId 下载原始/ASR/速记（open-vault record）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["search", "get"],
        default="search",
        help="search=列录音；get=下载内容；提供 --record/--record-file 时自动为 get",
    )
    parser.add_argument("-k", "--keyword", default=None, help="录音标题关键词（search 模式）")
    parser.add_argument("-st", "--start-time", default=None, help="创建时间起")
    parser.add_argument("-et", "--end-time", default=None, help="创建时间止")
    parser.add_argument(
        "--category-list",
        default=None,
        help="录音类型：upload,link,mobile,gtNote,pc,share 或中文（上传文件/手机录音等）",
    )
    parser.add_argument(
        "--space-type-list",
        default=None,
        help="所属区域：1/2 或 我的速记/租户速记，逗号分隔",
    )
    parser.add_argument(
        "--content-type",
        default="both",
        help="下载类型：original,asr,summary,both,all（get 模式）",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="录音 ID 或标题，逗号分隔；纯字母数字视为 ID 直接 get，否则 search，唯一完全匹配时自动 get",
    )
    parser.add_argument(
        "--record-file",
        default=None,
        help="csv 含 record_id 列（get 模式）",
    )
    parser.add_argument("-l", "--limit", type=int, default=DEFAULT_LIST_LIMIT, help="search 模式列表条数上限")
    parser.add_argument("--page-from", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=20, help="单页最大 50")
    parser.add_argument("-od", "--output-dir", default=None, help="结果保存目录路径（GTS_SAVE_FILE=True）")

    args = parser.parse_args()
    content_types = _normalize_content_types(args.content_type)
    categories = _normalize_category_list(_parse_str_list(args.category_list))
    space_types = _normalize_space_type_list(_parse_str_list(args.space_type_list))

    record_arg = args.record
    if args.record_file:
        try:
            record_arg = ",".join(_load_record_ids_from_file(args.record_file))
        except Exception as e:
            print(f"读取录音 ID 文件失败: {e}")
            sys.exit(1)

    print(
        private_record_finder(
            keyword=args.keyword,
            record_ids=record_arg,
            content_types=content_types,
            start_time=args.start_time,
            end_time=args.end_time,
            category_list=categories,
            space_type_list=space_types,
            limit=int(args.limit),
            page_from=args.page_from,
            page_size=args.page_size,
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
