import os
import sys
import requests
from pathlib import PureWindowsPath
from urllib.parse import unquote
from io import TextIOWrapper
from typing import List
from typing import Optional
import hashlib

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (COMPANY_ANNOUNCEMENT_DOWNLOAD_URL, FILE_TYPE_MAP, FILE_URL, FOREIGN_REPORT_DOWNLOAD_URL, REPORT_DOWNLOAD_URL, SUMMARY_DOWNLOAD_URL, WORK_PATH, check_version, file_dir, get_authorization_headers, get_authorization_token, get_headers_extra)

def _normalize_download_type(download_type: Optional[str]) -> str:
    return (download_type or "markdown").strip().lower()


def _download_type_fallback_chain(file_type: str, download_type: str) -> List[str]:
    dt = _normalize_download_type(download_type)
    if file_type in ("研究报告", "外资研报", "公司公告") and dt == "markdown":
        return ["pdf"]
    return []


def _uses_download_file_type(file_type: str) -> bool:
    return file_type in ("研究报告", "外资研报", "公司公告")


def _response_unavailable(response: requests.Response) -> Optional[str]:
    if response.status_code != 200:
        return f"{response.status_code} {response.text}"
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        try:
            data = response.json()
            if isinstance(data, dict) and data.get("url"):
                return f"不存在文件，为网络地址：{data['url']}"
            return str(data.get("message") or data)
        except Exception:
            return response.text[:200] or "JSON 响应无法解析"
    if not response.content:
        return "响应内容为空"
    return None


def _request_download(file_id: str, file_type: str, download_type: str, headers: dict) -> requests.Response:
    url_map = {
        "会议纪要": SUMMARY_DOWNLOAD_URL,
        "公司公告": COMPANY_ANNOUNCEMENT_DOWNLOAD_URL,
        "研究报告": REPORT_DOWNLOAD_URL,
        "外资研报": FOREIGN_REPORT_DOWNLOAD_URL,
    }
    download_type_map = {
        "pdf": 1,
        "markdown": 2,
    }
    params_map = {
        "会议纪要": {"summaryId": file_id},
        "研究报告": {
            "reportId": file_id,
            "fileType": download_type_map[download_type],
        },
        "外资研报": {
            "reportId": file_id,
            "fileType": download_type_map[download_type],
        },
        "公司公告": {
            "announcementId": file_id,
            "fileType": download_type_map[download_type],
        },
    }
    if file_type in url_map:
        if _uses_download_file_type(file_type) and download_type not in download_type_map:
            raise ValueError(f"不支持的下载类型: {download_type}")
        return requests.get(url_map[file_type], headers=headers, params=params_map[file_type], timeout=300)

    params = {
        "sourceId": file_id,
        "resourceType": FILE_TYPE_MAP[file_type],
    }
    return requests.get(FILE_URL, headers=headers, params=params, timeout=300)


def safe_file_title(file_item):
    title = file_item["title"]
    not_allow_title_symbol = [
        "\\", ":", "*", "?", "\"", "<", ">", "|",
        "=", "&", "\0",
    ]
    for symbol in not_allow_title_symbol:
        title = title.replace(symbol, "")
    title = title.replace(" ", "_")
    return title


def _is_windows_style_path(path: str) -> bool:
    if not path:
        return False
    if len(path) >= 3 and path[0].isalpha() and path[1] == ":":
        return True
    return "\\" in path


class UnsupportedClientPathError(ValueError):
    """远程非 Windows 服务无法写入客户端 Windows 路径。"""


def _strip_path_quotes(path: str) -> str:
    path = (path or "").strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        return path[1:-1]
    if path.startswith('"'):
        return path[1:]
    if path.endswith('"'):
        return path[:-1]
    return path


def _normalize_user_path(path: str) -> str:
    """规范化用户路径。

    - 本机 Windows：保留盘符，统一为系统分隔符。
    - 非 Windows 服务：拒绝 ``C:\\...`` 客户端路径（避免 ``\\U`` 正则/转义问题，且无法写入客户端盘）。
    """
    if not path:
        return path
    path = _strip_path_quotes(path)
    if not path:
        return path
    if _is_windows_style_path(path):
        forward = path.replace("\\", "/")
        if os.name == "nt":
            return os.path.normpath(str(PureWindowsPath(forward)))
        raise UnsupportedClientPathError(
            "检测到 Windows 路径（如 C:\\...）；远程 MCP 无法写入客户端本地磁盘。"
            "请勿传 output_dir/output，使用默认目录（返回附件或 OBS 下载链接）"
        )
    return os.path.normpath(path)


def _split_user_path(path: str) -> tuple[str, str]:
    """Return (directory, filename); handles Windows-style paths on Windows."""
    path = _normalize_user_path(path)
    if os.name == "nt" and _is_windows_style_path(path):
        p = PureWindowsPath(path.replace("\\", "/"))
        parent = str(p.parent)
        if parent in (".", ""):
            return "", p.name
        return parent, p.name
    return os.path.split(path)


def _join_user_path(directory: str, filename: str) -> str:
    if os.name == "nt" and (_is_windows_style_path(directory) or "\\" in directory):
        base = PureWindowsPath(directory.replace("\\", "/"))
        return str(base / filename)
    return os.path.join(directory, filename)


def _safe_output_path(path: str) -> str:
    """Sanitize filename only; preserve directory (incl. Windows drive letter)."""
    directory, filename = _split_user_path(path)
    if not filename:
        return _normalize_user_path(path)
    stem, ext = os.path.splitext(filename)
    safe_stem = safe_file_title({"title": stem, "url": ext or "."})
    safe_name = safe_stem + ext
    return _join_user_path(directory, safe_name) if directory else safe_name


def _replace_output_extension(output: str, new_ext: str) -> str:
    new_ext = new_ext.lstrip(".")
    if not new_ext:
        return output
    directory, filename = _split_user_path(output)
    stem = os.path.splitext(filename)[0]
    return _join_user_path(directory, f"{stem}.{new_ext}") if directory else f"{stem}.{new_ext}"

def get_file(
    file_id: str,
    file_type: str,
    output: str = None,
    download_type: str = "markdown", # pdf, markdown
    output_dir: str = None, # 该参数仅在download_files中使用
    title: str = None, # 该参数仅在download_files中使用
):
    title = title.replace("/", "_") if title else None
    try:
        try:
            if not check_version():
                print(f"[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
        except Exception as e:
            print(f"[WARNING] 检查 Gangtise skills 版本失败\n")
        headers = get_authorization_headers()

        requested_type = _normalize_download_type(download_type)
        try_types = [requested_type]
        if _uses_download_file_type(file_type):
            for alt in _download_type_fallback_chain(file_type, requested_type):
                if alt not in try_types:
                    try_types.append(alt)

        response = None
        used_type = requested_type
        attempt_errors: List[str] = []
        for dt in try_types:
            try:
                resp = _request_download(file_id, file_type, dt, headers)
            except ValueError as e:
                attempt_errors.append(f"{dt}: {e}")
                continue
            err = _response_unavailable(resp)
            if err is None:
                response = resp
                used_type = dt
                break
            attempt_errors.append(f"{dt}: {err}")

        if response is None:
            if len(attempt_errors) == 1:
                return f"获取文件失败：{attempt_errors[0].split(': ', 1)[-1]}"
            return "获取文件失败：" + "；".join(attempt_errors)

        downgrade_note = ""
        if used_type != requested_type:
            downgrade_note = f"（{requested_type} 不可用，已改下载 {used_type}）"

        return_message = ""
        path_warn = ""
        if output:
            try:
                output = _normalize_user_path(output)
            except UnsupportedClientPathError as e:
                path_warn = f"[WARNING]{e}\n"
                output = None
        if output:
            _, output_filename = _split_user_path(output)
            if len(response.headers["Content-Disposition"].lower().split("filename*=utf-8''")) > 1:
                resp_ext = unquote(
                    response.headers["Content-Disposition"].lower().split("filename*=utf-8''")[1]
                ).split(".")[-1]
                if os.path.splitext(output_filename)[1].lstrip(".") != resp_ext:
                    output = _replace_output_extension(output, resp_ext)
            elif len(response.headers["Content-Disposition"].lower().split("filename=")) > 1:
                resp_ext = unquote(
                    response.headers["Content-Disposition"].lower().split("filename=")[1]
                ).split(".")[-1]
                if os.path.splitext(output_filename)[1].lstrip(".") != resp_ext:
                    output = _replace_output_extension(output, resp_ext)
            output = _safe_output_path(output)
            abs_output = os.path.abspath(output)
            return_message = f"文件保存路径已自动修正，并保存到：`{abs_output}`"
        elif output_dir:
            try:
                output_dir = _normalize_user_path(output_dir)
            except UnsupportedClientPathError as e:
                path_warn = f"[WARNING]{e}\n"
                output_dir = None
        if not output and output_dir:
            if len(response.headers["Content-Disposition"].lower().split("filename*=utf-8''")) > 1:
                file_name = unquote(response.headers["Content-Disposition"].lower().split("filename*=utf-8''")[1])
            elif len(response.headers["Content-Disposition"].lower().split("filename=")) > 1:
                file_name = unquote(response.headers["Content-Disposition"].lower().split("filename=")[1])
            else:
                return path_warn + "获取文件失败：无法获取文件名"
            file_name = os.path.basename(file_name)
            if title:
                file_name = title + os.path.splitext(file_name)[1]
            output = _safe_output_path(_join_user_path(output_dir, file_name))
            abs_output = os.path.abspath(output)
            return_message = f"文件已保存到：`{abs_output}`"
        if not output:
            if len(response.headers["Content-Disposition"].lower().split("filename*=utf-8''")) > 1:
                file_name = unquote(response.headers["Content-Disposition"].lower().split("filename*=utf-8''")[1])
            elif len(response.headers["Content-Disposition"].lower().split("filename=")) > 1:
                file_name = unquote(response.headers["Content-Disposition"].lower().split("filename=")[1])
            else:
                return path_warn + "获取文件失败：无法获取文件名"
            file_name = os.path.basename(file_name)
            output = _safe_output_path(_join_user_path(file_dir, file_name))
            abs_output = os.path.abspath(output)
            return_message = f"文件已保存到：`{abs_output}`"
        if path_warn:
            return_message = path_warn + return_message
        output_dirname, _ = _split_user_path(output)
        if output_dirname:
            os.makedirs(output_dirname, exist_ok=True)
        if os.path.exists(output):
            with open(output, "rb") as f:
                existed_file_sha = hashlib.sha256(f.read()).hexdigest()
            response_content_sha = hashlib.sha256(response.content).hexdigest()
            if existed_file_sha == response_content_sha:
                return f"文件已存在：`{os.path.abspath(output)}`" + downgrade_note
            else:
                iteration = 1
                output_dirname, output_basename = _split_user_path(output)
                output_stem, output_ext = os.path.splitext(output_basename)
                while os.path.exists(output):
                    output = _join_user_path(
                        output_dirname,
                        f"{output_stem}({iteration}){output_ext}",
                    )
                    iteration += 1
                return_message = f"文件名重复，已自动重命名并下载到 `{os.path.abspath(output)}`"
        with open(output, "wb") as f:
            f.write(response.content)
        return return_message + downgrade_note
    except Exception as e:
        return f"获取文件失败：{str(e)}"

def download_files(files: List[dict], method_name: str, output_dir: Optional[str] = None, download_types: Optional[List[str]] = None):
    path_warn = ""
    if output_dir:
        try:
            target_dir = _normalize_user_path(output_dir)
        except UnsupportedClientPathError as e:
            path_warn = f"[WARNING]{e}\n"
            target_dir = os.path.join(WORK_PATH, method_name)
    else:
        target_dir = os.path.join(WORK_PATH, method_name)
    os.makedirs(target_dir, exist_ok=True)
    failed_message = []

    # 基于 (类型, 类型ID) 去重，避免重复下载同一文件
    unique_files: List[dict] = []
    seen: set[tuple[str, str]] = set()
    for file in files or []:
        file_type = str(file.get("类型", "") or "").strip()
        file_id = str(file.get("类型ID", "") or "").strip()
        if not file_type or not file_id:
            continue
        key = (file_type, file_id)
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(file)

    for file in unique_files:
        for download_type in (download_types or ["markdown"]):
            return_message = get_file(
                file["类型ID"],
                file["类型"],
                output=None,
                output_dir=target_dir,
                title=file.get("标题"),
                download_type=download_type,
            )
            if not any(
                key in return_message
                for key in ("文件已保存到", "文件名重复，已自动重命名", "文件保存路径已自动修正", "文件已存在")
            ):
                failed_message.append({"title": file["标题"], "message": return_message, "download_type": download_type, "web_file": True if file.get("网络连接", "") else False})
    if len(failed_message) == len(unique_files) and unique_files:
        return_message = "\n".join([f"- {x['title']}({x['download_type']})：{'是网络文件' if x['web_file'] else x['message']}" for x in failed_message])
    elif len(failed_message) > 0:
        return_message = f"部分文件下载成功，并保存到：`{os.path.abspath(target_dir)}`"
        return_message += "; 其中有下载失败的文件：\n" + "\n".join([f"- {x['title']}({x['download_type']})：{'是网络文件' if x['web_file'] else x['message']}" for x in failed_message])
    else:
        return_message = f"文件全部下载成功，并保存到：`{os.path.abspath(target_dir)}`"
    return path_warn + return_message

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="RAG 文件检索命令行：按查询语句检索相关文件。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-id", "--file-id", default="", help="文件ID")
    parser.add_argument("-type", "--file-type", default="", help="文件类型")
    parser.add_argument("-o", "--output", default="", help="输出文件路径")
    parser.add_argument("-dt", "--download-type", default="markdown", help="下载类型")

    args = parser.parse_args()

    file_id = args.file_id.strip()
    if not file_id:
        parser.error("必须提供文件ID：-id/--file-id")

    file_type = args.file_type.strip()
    if not file_type:
        parser.error("必须提供文件类型：-type/--file-type")

    out = get_file(
        file_id=file_id,
        file_type=file_type,
        output=args.output,
        download_type=args.download_type,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors='ignore')
    main()