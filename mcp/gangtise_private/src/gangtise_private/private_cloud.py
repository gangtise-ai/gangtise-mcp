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

from .utils import (DRIVE_DOWNLOAD_URL, DRIVE_LIST_URL, FILE_DEFAULT_LIMIT, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, is_code_arg)

DEFAULT_LIST_LIMIT = FILE_DEFAULT_LIMIT.get("private_cloud", 100)
IDS_FILE_HINT = "可以删除行或保留需要的文件，再通过 --files-file 参数下载"

FILE_TYPE_MAP = {
    "文档": "document",
    "图片": "image",
    "音视频": "media",
    "视频": "media",
    "音频": "media",
    "公众号文章": "article",
    "文章": "article",
    "其他": "other",
}

FILE_TYPE_VALID = {"document", "image", "media", "article", "other"}

SPACE_TYPE_MAP = {
    "我的云盘": 1,
    "租户云盘": 2,
    "1": 1,
    "2": 2,
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


def _normalize_file_type_list(items: Optional[List[str]]) -> Optional[List[str]]:
    if not items:
        return None
    out: List[str] = []
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        key = s.lower()
        if s in FILE_TYPE_MAP:
            v = FILE_TYPE_MAP[s]
        elif key in FILE_TYPE_VALID:
            v = key
        else:
            v = FILE_TYPE_MAP.get(s, s)
        if v in FILE_TYPE_VALID and v not in out:
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


def _flatten_drive_file(row: dict) -> dict:
    return {
        "file_id": row.get("fileId"),
        "title": row.get("title"),
        "create_time": row.get("createTime"),
        "space_type": row.get("spaceType"),
        "file_size": row.get("fileSize"),
        "url": row.get("url"),
        "uploader": row.get("uploader"),
    }


def _fetch_drive_list(
    headers: dict,
    *,
    page_from: int = 0,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keyword: Optional[str] = None,
    file_type_list: Optional[List[str]] = None,
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
        if file_type_list:
            payload["fileTypeList"] = file_type_list
        if space_type_list:
            payload["spaceTypeList"] = space_type_list

        try:
            r = authorized_request("POST", DRIVE_LIST_URL, headers=headers, json=payload, timeout=300)
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
            msg = _api_error_message(body, "云盘列表请求失败")
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
                aggregated.append(_flatten_drive_file(row))
        if len(raw_list) < chunk:
            break
        offset += len(raw_list)

    return aggregated, total, part_err


def _download_drive_file(
    headers: dict,
    file_id: str,
    title: str = "",
    create_time: str = "",
    url: str = "",
) -> Tuple[Optional[dict], Optional[str]]:
    fid = str(file_id).strip()
    if not fid:
        return None, "fileId 为空"

    params = {"fileId": fid}
    try:
        r = authorized_request("GET", DRIVE_DOWNLOAD_URL, headers=headers, params=params, timeout=300)
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

    if "text" in ctype_hdr or "json" in ctype_hdr or "xml" in ctype_hdr:
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
        "标题": title or fid,
        "文件时间": create_time or "",
        "文件内容": body_text,
        "链接": url or "",
        "文件ID": fid,
        "类型": "AI云盘",
        "类型ID": fid,
    }
    return item, None


def _load_file_ids_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"云盘文件列表不存在: {path}")
    df = pd.read_csv(path)
    for col in ("file_id", "fileId", "类型ID"):
        if col in df.columns:
            return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]
    raise ValueError("文件须包含 file_id 或 fileId 列")


def _list_response_parts(records: List[dict]) -> List[dict]:
    return [{"data": records, "module": "private_cloud_list", "type": "data"}]


def _exact_match_files(keyword: str, records: List[dict]) -> List[dict]:
    kw = keyword.strip()
    return [
        r
        for r in records
        if str(r.get("title") or "").strip() == kw
        or str(r.get("file_id") or "").strip() == kw
    ]


def _resolve_files_from_arg(
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
        out = private_cloud_search(keyword=token, limit=limit, append_file_hint=False, **base_kwargs)
        headers, err = _auth_headers()
        if err:
            return None, err
        records, _, _ = _fetch_drive_list(
            headers,
            keyword=token,
            max_total=limit,
            page_from=base_kwargs.get("page_from", 0),
            page_size=base_kwargs.get("page_size", 20),
            start_time=base_kwargs.get("start_time"),
            end_time=base_kwargs.get("end_time"),
            file_type_list=base_kwargs.get("file_type_list"),
            space_type_list=base_kwargs.get("space_type_list"),
        )
        if not records:
            return None, f"未找到与「{token}」相关的云盘文件"
        exact = _exact_match_files(token, records)
        if len(exact) == 1:
            fid = str(exact[0].get("file_id") or "").strip()
            if fid:
                resolved.append(fid)
            continue
        if len(exact) > 1:
            prefix = f"「{token}」存在 {len(exact)} 条完全匹配，请指定更精确的标题或 ID：\n\n"
        else:
            prefix = f"未找到与「{token}」完全匹配的云盘文件，以下为候选结果：\n\n"
        search_outputs.append(prefix + out)

    if search_outputs:
        return None, "\n\n".join(search_outputs)
    return resolved, None


def private_cloud_search(
    keyword: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    file_type_list: Optional[List[str]] = None,
    space_type_list: Optional[List[int]] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output_dir: Optional[str] = None,
    append_file_hint: bool = False,
):
    usage: dict = {}
    headers, err = _auth_headers()
    if err:
        return format_response({"state": "error", "message": err, "data": [], "usage": usage}, "private_cloud", output_dir=output_dir)

    records, total, part_err = _fetch_drive_list(
        headers,
        page_from=page_from,
        page_size=page_size,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        file_type_list=file_type_list,
        space_type_list=space_type_list,
        max_total=limit,
    )
    if not records:
        return format_response(
            {"state": "error", "message": part_err or "未找到云盘文件", "data": [], "usage": usage},
            "private_cloud",
            output_dir=output_dir,
        )

    msg = f"已找到 {len(records)} 个云盘文件"
    if total:
        msg += f"（total={total}）"
    if append_file_hint:
        msg += f"；{IDS_FILE_HINT}"
    extra = f"\n未完整拉取：{part_err}" if part_err else ""
    return format_response(
        {"state": "success", "message": msg, "data": _list_response_parts(records), "usage": usage},
        "private_cloud",
        output_dir=output_dir,
        additional_message=extra.strip(),
    )


def private_cloud_get(
    file_ids: List[str],
    meta_by_id: Optional[Dict[str, dict]] = None,
    output_dir: Optional[str] = None,
):
    usage: dict = {}
    headers, err = _auth_headers()
    if err:
        return format_response({"state": "error", "message": err, "data": [], "usage": usage}, "private_cloud", output_dir=output_dir)

    ids = [str(x).strip() for x in file_ids if str(x).strip()]
    if not ids:
        return format_response(
            {
                "state": "error",
                "message": "未提供 fileId，请使用 --files 或先运行 search 获取列表",
                "data": [],
                "usage": usage,
            },
            "private_cloud",
            output_dir=output_dir,
        )

    files: List[dict] = []
    errs: List[str] = []
    meta_by_id = meta_by_id or {}

    for fid in ids:
        meta = meta_by_id.get(fid) or {}
        item, derr = _download_drive_file(
            headers,
            fid,
            title=str(meta.get("title") or ""),
            create_time=str(meta.get("create_time") or ""),
            url=str(meta.get("url") or ""),
        )
        if derr:
            errs.append(f"{fid}: {derr}")
            continue
        if item:
            files.append(item)

    if not files:
        return format_response(
            {
                "state": "error",
                "message": "；".join(errs) if errs else "未下载到云盘文件",
                "data": [],
                "usage": usage,
            },
            "private_cloud",
            output_dir=output_dir,
        )

    parts = [{"data": files, "module": "private_cloud_content", "type": "files"}]
    msg = f"已下载 {len(files)} 个云盘文件"
    if errs:
        msg += f"；部分失败：{'；'.join(errs[:3])}"
    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "private_cloud",
        output_dir=output_dir,
    )


def private_cloud_finder(
    keyword: Optional[str] = None,
    file_ids: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    file_type_list: Optional[List[str]] = None,
    space_type_list: Optional[List[int]] = None,
    limit: int = DEFAULT_LIST_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output_dir: Optional[str] = None,
):
    """检索云盘文件列表，或在提供 file_ids 时下载文件内容。"""
    common = dict(
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        file_type_list=file_type_list,
        space_type_list=space_type_list,
        limit=int(limit),
        page_from=page_from,
        page_size=page_size,
        output_dir=output_dir,
    )
    ids: List[str] = []
    if file_ids and str(file_ids).strip():
        resolved, err = _resolve_files_from_arg(str(file_ids).strip(), int(limit), **common)
        if err:
            return err
        ids = resolved or []
    if ids:
        return private_cloud_get(file_ids=ids, output_dir=output_dir)
    return private_cloud_search(**common, append_file_hint=True)


def main():
    import argparse

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise private 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise private 版本失败\n")

    parser = argparse.ArgumentParser(
        description="AI云盘：检索文件列表或按 fileId 下载（open-vault drive）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["search", "get"],
        default="search",
        help="search=列文件；get=下载；提供 --files/--files-file 时自动为 get",
    )
    parser.add_argument("-k", "--keyword", default=None, help="文件标题关键词（search 模式）")
    parser.add_argument("-st", "--start-time", default=None, help="创建时间起")
    parser.add_argument("-et", "--end-time", default=None, help="创建时间止")
    parser.add_argument(
        "--file-type-list",
        default=None,
        help="文件类型：document,image,media,article,other 或中文（文档/图片等）",
    )
    parser.add_argument(
        "--space-type-list",
        default=None,
        help="所属区域：1/2 或 我的云盘/租户云盘",
    )
    parser.add_argument(
        "--files",
        default=None,
        help="文件 ID 或标题，逗号分隔；纯字母数字视为 ID 直接 get，否则 search，唯一完全匹配时自动 get",
    )
    parser.add_argument(
        "--files-file",
        default=None,
        help="csv 含 file_id 列（get 模式）",
    )
    parser.add_argument("-l", "--limit", type=int, default=DEFAULT_LIST_LIMIT, help="search 模式列表条数上限")
    parser.add_argument("--page-from", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=20, help="单页最大 50")
    parser.add_argument("-od", "--output-dir", default=None, help="结果保存目录路径（GTS_SAVE_FILE=True）")

    args = parser.parse_args()
    file_types = _normalize_file_type_list(_parse_str_list(args.file_type_list))
    space_types = _normalize_space_type_list(_parse_str_list(args.space_type_list))

    file_ids_arg = args.files
    if args.files_file:
        try:
            file_ids_arg = ",".join(_load_file_ids_from_file(args.files_file))
        except Exception as e:
            print(f"读取 file_id 文件失败: {e}")
            sys.exit(1)

    print(
        private_cloud_finder(
            keyword=args.keyword,
            file_ids=file_ids_arg,
            start_time=args.start_time,
            end_time=args.end_time,
            file_type_list=file_types,
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
