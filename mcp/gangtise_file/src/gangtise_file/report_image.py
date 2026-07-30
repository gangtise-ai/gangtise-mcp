import os
import sys
from io import TextIOWrapper
from typing import List, Optional

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (check_version, DOWNLOAD_DEFAULT, FILE_DEFAULT_LIMIT, FILE_DOWNLOAD_DEFAULT_LIMIT, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, REPORT_IMAGE_DOWNLOAD_URL, REPORT_IMAGE_URL, resolve_result_limit, WORK_PATH)
from .get_file import (  # noqa: E402
    _download_success_info,
    _format_download_summary,
    _join_user_path,
    _normalize_user_path,
    _parse_content_disposition_filename,
    _unique_path_in_dir,
    safe_file_title,
)

TOP_MAX = 20
_VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _normalize_api_time(value: Optional[str], end_of_day: bool) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    if len(text) <= 10:
        day = text[:10]
        return f"{day} 23:59:59" if end_of_day else f"{day} 00:00:00"
    return text


def _join_str_list(items: object) -> str:
    if not isinstance(items, list):
        return ""
    parts = [str(x).strip() for x in items if str(x).strip()]
    return "；".join(parts)


def _format_report_image_item(images: List[dict]) -> List[dict]:
    results = []
    for image in images:
        page = image.get("page")
        total_pages = image.get("totalPages")
        page_display = ""
        if page is not None and total_pages is not None:
            page_display = f"{page}/{total_pages}"
        elif page is not None:
            page_display = str(page)

        item = {
            "标题": _join_str_list(image.get("imageCaption")),
            "来源标题": str(image.get("title") or "").strip(),
            "文件时间": str(image.get("publishTime") or "").strip(),
            "来源研报机构": str(image.get("broker") or "").strip(),
            "来源研报类别": str(image.get("category") or "").strip(),
            "来源研报标签": _join_str_list(image.get("typeList")),
            "来源研报行业": str(image.get("industry") or "").strip(),
            "图片所在页码": page_display,
            "脚注": _join_str_list(image.get("imageFootnote")),
            "图片内容": str(image.get("pageContent") or "").strip(),
            "来源研报ID": str(image.get("sourceId") or "").strip(),
            "类型": "研报图片",
            "类型ID": str(image.get("chunkId") or "").strip(),
        }
        results.append(item)
    return results


def _guess_image_ext(content_type: str, filename: str = "") -> str:
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    if "bmp" in ct:
        return ".bmp"
    if filename and "." in filename:
        ext = os.path.splitext(filename)[1]
        if ext:
            return ext
    return ".png"


def _sanitize_image_filename(name: str, content_type: str) -> str:
    cleaned = os.path.basename((name or "").strip())
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    cleaned = cleaned.strip().strip('"').strip("'")
    if not cleaned:
        return ""
    stem, ext = os.path.splitext(cleaned)
    if ext.lower() not in _VALID_IMAGE_EXTS:
        ext = _guess_image_ext(content_type, cleaned)
        cleaned = f"{stem or 'image'}{ext}"
    return cleaned


def _build_image_filename(
    resp_filename: Optional[str],
    content_type: str,
    title: str,
    source_title: str,
    page_display: str,
    chunk_id: str,
) -> str:
    if resp_filename:
        cleaned = _sanitize_image_filename(resp_filename, content_type)
        if cleaned:
            return cleaned

    label = title or source_title or chunk_id
    safe_label = safe_file_title({"title": label, "url": ""}) or chunk_id
    ext = _guess_image_ext(content_type, resp_filename or "")
    page_suffix = ""
    if page_display:
        page_num = page_display.split("/")[0].strip()
        if page_num:
            page_suffix = f"_p{page_num}"
    return f"{safe_label}{page_suffix}{ext}"


def _download_report_image(
    chunk_id: str,
    title: str,
    source_title: str,
    page_display: str,
    output_dir: str,
    headers: dict,
) -> str:
    response = requests.get(
        REPORT_IMAGE_DOWNLOAD_URL,
        headers=headers,
        params={"chunkId": chunk_id},
        timeout=300,
    )
    if response.status_code != 200:
        return f"获取图片失败：HTTP {response.status_code} {response.text[:200]}"

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        try:
            data = response.json()
            if isinstance(data, dict):
                return f"获取图片失败：{data.get('msg') or data.get('message') or data}"
            return f"获取图片失败：{data}"
        except Exception:
            return f"获取图片失败：{response.text[:200]}"

    if not response.content:
        return "获取图片失败：响应内容为空"

    resp_filename = _parse_content_disposition_filename(response.headers)
    file_name = _build_image_filename(
        resp_filename,
        content_type,
        title,
        source_title,
        page_display,
        chunk_id,
    )

    output_path = _unique_path_in_dir(output_dir, file_name)
    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(response.content)
    return f"文件已保存到：{os.path.abspath(output_path)}\n- {os.path.abspath(output_path)}"


def _download_report_images(
    images: List[dict],
    output_dir: Optional[str] = None,
) -> str:
    target_dir = _normalize_user_path(output_dir) if output_dir else os.path.join(
        WORK_PATH, "report_image"
    )
    os.makedirs(target_dir, exist_ok=True)

    headers = get_authorization_headers()

    failed_messages = []
    total_saved_files = 0
    all_saved_paths: List[str] = []

    unique_images: List[dict] = []
    seen: set[str] = set()
    for image in images or []:
        chunk_id = str(image.get("类型ID", "") or "").strip()
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        unique_images.append(image)

    for image in unique_images:
        return_message = _download_report_image(
            image["类型ID"],
            image.get("标题", ""),
            image.get("来源标题", ""),
            image.get("图片所在页码", ""),
            target_dir,
            headers,
        )
        ok, n_files, _, paths = _download_success_info(return_message)
        if ok:
            total_saved_files += n_files
            for p in paths:
                ap = os.path.abspath(p)
                if ap not in all_saved_paths:
                    all_saved_paths.append(ap)
        else:
            failed_messages.append({
                "title": image.get("标题", ""),
                "message": return_message,
            })

    if len(failed_messages) == len(unique_images) and unique_images:
        return "\n".join(
            f"- {x['title']}：{x['message']}" for x in failed_messages
        )
    if failed_messages:
        return_message = _format_download_summary(
            "部分图片下载成功", total_saved_files, all_saved_paths
        )
        return_message += "; 其中有下载失败的图片：\n" + "\n".join(
            f"- {x['title']}：{x['message']}" for x in failed_messages
        )
        return return_message
    return _format_download_summary("图片全部下载成功", total_saved_files, all_saved_paths)


def report_image_finder(
    keyword: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source_id: Optional[str] = None,
    limit: Optional[int] = None,
    download: bool = False,
    output_dir: Optional[str] = None,
):
    try:
        limit = resolve_result_limit(limit, download, "report_image")
        keyword_str = (keyword or "").strip()
        if not keyword_str:
            return format_response(
                {"state": "error", "message": "关键词 keyword 不能为空"},
                "report_image",
            )

        headers = get_authorization_headers()

        payload = {
            "keyword": keyword_str,
            "top": max(1, min(int(limit), TOP_MAX)),
        }
        start_time = _normalize_api_time(start_date, end_of_day=False)
        end_time = _normalize_api_time(end_date, end_of_day=True)
        if start_time:
            payload["startTime"] = start_time
        if end_time:
            payload["endTime"] = end_time
        if source_id and str(source_id).strip():
            payload["sourceId"] = str(source_id).strip()

        response = requests.post(REPORT_IMAGE_URL, headers=headers, json=payload, timeout=120)
        if response.status_code != 200:
            return format_response(
                {
                    "state": "error",
                    "message": response.text.replace("\n", " ").replace("\r", " ").strip(),
                },
                "report_image",
            )

        result = response.json()
        if result.get("code") not in [200, "000000"] and result.get("status") is not True:
            return format_response(
                {
                    "state": "error",
                    "message": str(result.get("msg") or result.get("message") or "请求失败"),
                },
                "report_image",
            )

        images = result.get("data") or []
        if not isinstance(images, list):
            return format_response(
                {"state": "error", "message": "接口返回 data 格式异常"},
                "report_image",
            )
        if not images:
            return format_response(
                {
                    "state": "error",
                    "message": "未找到相关研报图片，建议修改查询条件",
                    "data": [],
                },
                "report_image",
            )

        formatted = _format_report_image_item(images)

        additional_message = None
        if download:
            additional_message = _download_report_images(formatted, output_dir)

        response_data = {
            "state": "success",
            "message": "已找到相关研报图片",
            "data": [{"data": formatted, "module": "report_image", "type": "files"}],
        }
        return format_response(
            response_data,
            "report_image",
            additional_message=additional_message,
        )
    except Exception as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "report_image",
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="研报图片检索：按关键词搜索研报中的图片，返回元数据及 chunkId。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-k",
        "--keyword",
        required=True,
        help="搜索关键词，如 AI、新能源汽车",
    )
    parser.add_argument(
        "-sd",
        "--start-date",
        default="",
        help="开始时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
    )
    parser.add_argument(
        "-ed",
        "--end-date",
        default="",
        help="结束时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
    )
    parser.add_argument(
        "--source-id",
        default="",
        help="研报 ID，用于限定特定研报来源",
    )
    parser.add_argument(
        "-l",
        "--limit",
        default=None,
        type=int,
        help="返回条数上限；不传时用检索默认，开启 -d 下载时默认 5",
    )
    parser.add_argument(
        "-d",
        "--download",
        default=DOWNLOAD_DEFAULT,
        type=bool,
        help="是否在检索后自动下载图片原文件（0.1 积分/张）",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="下载图片保存路径，建议使用绝对路径",
    )

    args = parser.parse_args()

    output_dir = args.output_dir or None
    if not args.download and output_dir:
        print("[WARNING] 参数 -od/--output-dir 仅在下载文件时有效，已忽略\n")
        output_dir = None

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise skills 版本失败\n")

    out = report_image_finder(
        keyword=args.keyword,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        source_id=args.source_id or None,
        limit=resolve_result_limit(args.limit, bool(args.download), "report_image"),
        download=args.download or False,
        output_dir=output_dir,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
