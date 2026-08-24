import os
import sys
import io
import re
import zipfile
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

from .utils import (COMPANY_ANNOUNCEMENT_DOWNLOAD_URL, FILE_TYPE_MAP, FILE_URL, FOREIGN_REPORT_DOWNLOAD_URL, HK_ANNOUNCEMENT_DOWNLOAD_URL, INDEPENDENT_OPINION_DOWNLOAD_URL, OFFICIAL_ACCOUNT_DOWNLOAD_URL, PAMIRS_SUMMARY_DOWNLOAD_URL, PERFORMANCE_CALENDAR_DOWNLOAD_URL, REPORT_DOWNLOAD_URL, SUMMARY_DOWNLOAD_URL, TRY_MORE_DOWNLOAD, US_ANNOUNCEMENT_DOWNLOAD_URL, WORK_PATH, authorized_request, check_version, file_dir, get_authorization_headers, get_authorization_token, get_headers_extra)

def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _repair_filename(name: str) -> str:
    """修正 zip / Content-Disposition 中 UTF-8 被误按 cp437 等解码的文件名。"""
    if not name:
        return name
    if name.isascii():
        return name
    for src, dst in (
        ("cp437", "utf-8"),
        ("cp437", "gbk"),
        ("cp1252", "utf-8"),
        ("cp1252", "gbk"),
        ("latin1", "utf-8"),
        ("latin1", "gbk"),
    ):
        try:
            candidate = name.encode(src).decode(dst)
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if candidate != name and "\ufffd" not in candidate and _has_cjk(candidate):
            return candidate
    return name


def _parse_content_disposition_filename(headers) -> Optional[str]:
    cd = headers.get("Content-Disposition") or headers.get("content-disposition")
    if not cd:
        return None

    def _finalize_filename(raw: str) -> str:
        name = (raw or "").strip().strip('"').strip("'")
        # 部分接口未加引号，filename 后会跟 ", attachment" 等额外文本。
        if "," in name:
            name = name.split(",", 1)[0].strip().strip('"').strip("'")
        return _repair_filename(unquote(name))

    match = re.search(r"filename\*=(?:UTF-8|utf-8)''([^;\n]+)", cd, re.IGNORECASE)
    if match:
        return _finalize_filename(match.group(1).strip())

    match = re.search(r"filename\*=([^;\n]+)", cd, re.IGNORECASE)
    if match:
        part = match.group(1).strip().strip('"')
        if "''" in part:
            return _finalize_filename(part.split("''", 1)[-1].strip())
        return _finalize_filename(part)

    match = re.search(r'filename\s*=\s*"((?:\\.|[^"\\])*)"', cd, re.IGNORECASE)
    if match:
        raw = match.group(1).replace('\\"', '"')
        return _finalize_filename(raw)

    match = re.search(r"filename\s*=\s*([^;\n]+)", cd, re.IGNORECASE)
    if match:
        return _finalize_filename(match.group(1))
    return None


def _zip_entry_filename(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return _repair_filename(info.filename)
    for src, dst in (("cp437", "utf-8"), ("cp437", "gbk")):
        try:
            return info.filename.encode(src).decode(dst)
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return _repair_filename(info.filename)


def _normalize_download_type(download_type: Optional[str]) -> str:
    return (download_type or "markdown").strip().lower()


def _official_account_file_type(download_type: Optional[str]) -> int:
    """公众号 download/file：1 txt，2 HTML。"""
    s = _normalize_download_type(download_type)
    if s in ("2", "html"):
        return 2
    return 1


def _foreign_report_file_type(download_type: Optional[str]) -> int:
    """
    外资研报 download/file：1 原始 PDF，2 Markdown，3 中文翻译 PDF，4 中文翻译 Markdown。
    """
    s = (download_type or "pdf").strip().lower()
    if s in ("4", "zh_markdown", "markdown_zh", "zh-md", "zhmd", "中文markdown", "翻译markdown", "中文md"):
        return 4
    if s in ("3", "zh_pdf", "pdf_zh", "zh-pdf", "中文pdf", "翻译pdf", "中文"):
        return 3
    if s in ("2", "markdown", "md"):
        return 2
    if s in ("1", "pdf", "original", "original_pdf"):
        return 1
    raise ValueError(
        f"外资研报不支持的下载类型: {download_type}，"
        "可选 pdf、markdown、zh_pdf、zh_markdown（或 1/2/3/4）"
    )


def _download_type_fallback_chain(file_type: str, download_type: str) -> List[str]:
    """markdown / 中文 HTML 不可用时依次尝试的备用格式。"""
    dt = _normalize_download_type(download_type)
    if file_type == "外资独立观点":
        zh_aliases = {"zh", "中文", "translation", "html_zh", "zh_html", "chinese", "翻译", "2"}
        if dt in zh_aliases:
            return ["html"]
        if dt == "markdown":
            return ["html"]
        return []
    if file_type == "外资研报":
        zh_md_aliases = {
            "4", "zh_markdown", "markdown_zh", "zh-md", "zhmd",
            "中文markdown", "翻译markdown", "中文md",
        }
        zh_pdf_aliases = {
            "3", "zh_pdf", "pdf_zh", "zh-pdf", "中文pdf", "翻译pdf", "中文",
        }
        if dt in zh_md_aliases:
            return ["markdown", "zh_pdf", "pdf"]
        if dt in zh_pdf_aliases:
            return ["pdf", "markdown"]
        if dt == "markdown":
            return ["pdf"]
        return []
    if file_type in ("研究报告", "公司公告", "美股公告") and dt == "markdown":
        return ["pdf"]
    if file_type == "公众号" and dt == "html":
        return ["txt"]
    if file_type == "公众号" and dt in ("txt", "markdown"):
        return ["html"]
    return []


def _pamirs_summary_file_type(download_type: Optional[str]) -> int:
    """帕米尔专家纪要 download/file：1 原始文件，2 HTML。"""
    s = (download_type or "original").strip().lower()
    if s in ("2", "html", "markdown", "md"):
        return 2
    if s in ("1", "original", "pdf", "raw", "原文", "原始文件"):
        return 1
    raise ValueError(
        f"帕米尔专家纪要不支持的下载类型: {download_type}，"
        "可选 original、html（或 1/2）"
    )


def _uses_download_file_type(file_type: str) -> bool:
    return file_type in (
        "研究报告",
        "外资研报",
        "公司公告",
        "美股公告",
        "外资独立观点",
        "公众号",
        "帕米尔专家纪要",
    )


def _response_unavailable(response: requests.Response, file_type: str | None = None) -> Optional[str]:
    if response.status_code != 200:
        return f"{response.status_code} {response.text}"
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        try:
            data = response.json()
            if isinstance(data, dict):
                # 公众号列表常带原文链接；不能因有 url 字段就视为不可下载
                if file_type != "公众号" and data.get("url"):
                    return f"不存在文件，为网络地址：{data['url']}"
                return str(data.get("msg") or data.get("message") or data)
            return str(data)
        except Exception:
            return response.text[:200] or "JSON 响应无法解析"
    if not response.content:
        return "响应内容为空"
    return None


def _build_download_params(file_id: str, file_type: str, download_type: str) -> dict:
    """按 file_type 惰性构造 download 参数，避免误调用其他类型的 fileType 解析。"""
    download_type_map = {
        "pdf": 1,
        "markdown": 2,
        "txt": 1,
        "html": 2,
    }
    if file_type == "会议纪要":
        return {"summaryId": file_id}
    if file_type == "帕米尔专家纪要":
        return {
            "summaryId": file_id,
            "fileType": _pamirs_summary_file_type(download_type),
        }
    if file_type == "研究报告":
        return {
            "reportId": file_id,
            "fileType": download_type_map.get(download_type, 1),
        }
    if file_type == "外资研报":
        return {
            "reportId": file_id,
            "fileType": _foreign_report_file_type(download_type),
        }
    if file_type == "公司公告":
        return {
            "announcementId": file_id,
            "fileType": download_type_map.get(download_type, 1),
        }
    if file_type == "美股公告":
        return {
            "announcementId": file_id,
            "fileType": download_type_map.get(download_type, 1),
        }
    if file_type == "港股公告":
        return {"announcementId": file_id}
    if file_type == "外资独立观点":
        return {
            "independentOpinionId": file_id,
            "fileType": _independent_opinion_file_type(download_type),
        }
    if file_type == "公众号":
        return {
            "articleId": file_id,
            "fileType": _official_account_file_type(download_type),
        }
    if file_type == "财报日历":
        # 仅支持下载已发布（hasAttachment=true）的业绩报告原文件
        return {"performanceReportId": file_id}
    raise ValueError(f"不支持的文件类型: {file_type}")


def _request_download(file_id: str, file_type: str, download_type: str, headers: dict) -> requests.Response:
    url_map = {
        "会议纪要": SUMMARY_DOWNLOAD_URL,
        "帕米尔专家纪要": PAMIRS_SUMMARY_DOWNLOAD_URL,
        "公司公告": COMPANY_ANNOUNCEMENT_DOWNLOAD_URL,
        "港股公告": HK_ANNOUNCEMENT_DOWNLOAD_URL,
        "美股公告": US_ANNOUNCEMENT_DOWNLOAD_URL,
        "研究报告": REPORT_DOWNLOAD_URL,
        "外资研报": FOREIGN_REPORT_DOWNLOAD_URL,
        "外资独立观点": INDEPENDENT_OPINION_DOWNLOAD_URL,
        "公众号": OFFICIAL_ACCOUNT_DOWNLOAD_URL,
        "财报日历": PERFORMANCE_CALENDAR_DOWNLOAD_URL,
    }
    if file_type in url_map:
        params = _build_download_params(file_id, file_type, download_type)
        response = authorized_request("GET", url_map[file_type], headers=headers, params=params, timeout=300)
        return response

    params = {
        "sourceId": file_id,
        "resourceType": FILE_TYPE_MAP[file_type],
    }
    return authorized_request("GET", FILE_URL, headers=headers, params=params, timeout=300)


def _independent_opinion_file_type(download_type: Optional[str]) -> int:
    """
    外资独立观点 download/file：1 原文 HTML，2 中文翻译 HTML。
    与研报类 pdf/markdown 参数不同，此处用 html / zh 等别名。
    """
    s = (download_type or "html").strip().lower()
    if s in ("2", "zh", "translation", "html_zh", "zh_html", "chinese", "中文", "翻译"):
        return 2
    if s in ("1", "html", "original", "html_original", "original_html", "原文", "原文 HTML"):
        return 1
    raise ValueError(
        f"外资独立观点不支持的下载类型: {download_type}，"
        "可选 html、html_zh（或 zh、1/2）"
    )


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
        # PureWindowsPath('.') parent edge
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


def _is_zip_payload(content: bytes, filename: str = "") -> bool:
    if content[:2] == b"PK":
        return True
    return str(filename or "").lower().endswith(".zip")


def _unique_path_in_dir(dest_dir: str, filename: str) -> str:
    dest = _safe_output_path(_join_user_path(dest_dir, filename))
    if not os.path.exists(dest):
        return dest
    directory, basename = _split_user_path(dest)
    stem, ext = os.path.splitext(basename)
    iteration = 1
    while os.path.exists(dest):
        dest = _join_user_path(directory, f"{stem}({iteration}){ext}")
        iteration += 1
    return dest


def _unique_dir_in_dir(dest_dir: str, dirname: str) -> str:
    safe_name = safe_file_title({"title": dirname, "url": ""}) or "extract"
    dest = _join_user_path(dest_dir, safe_name)
    if not os.path.exists(dest):
        return dest
    iteration = 1
    while os.path.exists(dest):
        dest = _join_user_path(dest_dir, f"{safe_name}({iteration})")
        iteration += 1
    return dest


def _announcement_extract_subdir(title: Optional[str], file_id: str, zip_stem: str) -> str:
    if title:
        base = safe_file_title({"title": title, "url": ""})
    else:
        base = safe_file_title({"title": zip_stem, "url": ""})
    fid = str(file_id or "").strip()
    if base and fid:
        return f"{base}_{fid}"
    return base or fid or zip_stem or "announcement"


def _extract_zip_preserve(content: bytes, dest_dir: str, subdir: str) -> tuple[List[str], str]:
    """解压 zip 到 dest_dir/subdir，保留压缩包内目录结构，便于区分多份公告。"""
    extract_root = _unique_dir_in_dir(dest_dir, subdir)
    os.makedirs(extract_root, exist_ok=True)
    extracted: List[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename:
                continue
            rel = _zip_entry_filename(info).replace("\\", "/")
            parts = [_repair_filename(p) for p in rel.split("/") if p and p != "."]
            if not parts:
                continue
            out_dir = extract_root
            for part in parts[:-1]:
                out_dir = _join_user_path(out_dir, part)
            os.makedirs(out_dir, exist_ok=True)
            out_path = _unique_path_in_dir(out_dir, parts[-1])
            with zf.open(info) as src, open(out_path, "wb") as dst:
                dst.write(src.read())
            extracted.append(out_path)
    return extracted, extract_root


def _us_announcement_extract_dir(
    output_dirname: str,
    output_dir: Optional[str],
) -> str:
    if output_dirname:
        return output_dirname
    if output_dir:
        return _normalize_user_path(output_dir)
    return os.path.join(WORK_PATH, "announcement")


def _success_message_with_paths(headline: str, paths: List[str], note: str = "") -> str:
    """成功消息 + 绝对路径列表（普通文件路径或 zip 解压目录，路径用反引号包裹便于匹配）。"""
    ordered: List[str] = []
    seen: set = set()
    for p in paths:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            ordered.append(ap)
    msg = headline
    if ordered:
        msg += "\n" + "\n".join(f"- `{p}`" for p in ordered)
    return msg + note


def _strip_path_ticks(path: str) -> str:
    p = (path or "").strip()
    if len(p) >= 2 and p[0] == "`" and p[-1] == "`":
        return p[1:-1]
    return p


def _paths_from_success_message(message: str) -> List[str]:
    paths: List[str] = []
    for line in (message or "").splitlines():
        s = line.strip()
        if s.startswith("- "):
            paths.append(_strip_path_ticks(s[2:]))
        else:
            for m in re.finditer(r"`([^`\n]+)`", s):
                paths.append(m.group(1).strip())
    return paths


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
        except Exception:
            print(f"[WARNING] 检查 Gangtise skills 版本失败\n")
        headers = get_authorization_headers()

        requested_type = _normalize_download_type(download_type)
        if file_type == "公众号" and (download_type is None or not str(download_type).strip()):
            requested_type = "txt"
        if file_type == "帕米尔专家纪要":
            # get_file 默认 download_type=markdown；帕米尔仅支持 original/html
            if download_type is None or not str(download_type).strip():
                requested_type = "original"
            elif requested_type in ("markdown", "md"):
                requested_type = "html"
        try_types = [requested_type]
        if TRY_MORE_DOWNLOAD:
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
            err = _response_unavailable(resp, file_type)
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
        resp_filename = _parse_content_disposition_filename(response.headers)
        if output:
            try:
                output = _normalize_user_path(output)
            except UnsupportedClientPathError as e:
                path_warn = f"[WARNING]{e}\n"
                output = None
        if output:
            _, output_filename = _split_user_path(output)
            if resp_filename:
                resp_ext = os.path.splitext(resp_filename)[1].lstrip(".")
                if resp_ext and os.path.splitext(output_filename)[1].lstrip(".") != resp_ext:
                    output = _replace_output_extension(output, resp_ext)
            output = _safe_output_path(output)
            abs_output = os.path.abspath(output)
            return_message = _success_message_with_paths(
                f"文件保存路径已自动修正，并保存到：`{abs_output}`",
                [abs_output],
            )
        elif output_dir:
            try:
                output_dir = _normalize_user_path(output_dir)
            except UnsupportedClientPathError as e:
                path_warn = f"[WARNING]{e}\n"
                output_dir = None
        if not output and output_dir:
            if not resp_filename:
                return path_warn + "获取文件失败：无法获取文件名"
            file_name = os.path.basename(resp_filename)
            if title:
                file_name = safe_file_title({"title": title, "url": ""}) + os.path.splitext(file_name)[1]
            output = _safe_output_path(_join_user_path(output_dir, file_name))
            abs_output = os.path.abspath(output)
            return_message = _success_message_with_paths(
                f"文件已保存到：`{abs_output}`",
                [abs_output],
            )
        if not output:
            if not resp_filename:
                return path_warn + "获取文件失败：无法获取文件名"
            file_name = os.path.basename(resp_filename)
            output = _safe_output_path(_join_user_path(file_dir, file_name))
            abs_output = os.path.abspath(output)
            return_message = _success_message_with_paths(
                f"文件已保存到：`{abs_output}`",
                [abs_output],
            )
        if path_warn:
            return_message = path_warn + return_message
        output_dirname, output_basename = _split_user_path(output)
        if output_dirname:
            os.makedirs(output_dirname, exist_ok=True)

        if (
            file_type == "美股公告"
            and _is_zip_payload(response.content, output_basename)
        ):
            extract_dir = _us_announcement_extract_dir(output_dirname, output_dir)
            zip_stem = os.path.splitext(output_basename)[0]
            subdir = _announcement_extract_subdir(title, file_id, zip_stem)
            extracted, extract_root = _extract_zip_preserve(response.content, extract_dir, subdir)
            if not extracted:
                return "获取文件失败：zip 压缩包内无有效文件" + downgrade_note
            abs_root = os.path.abspath(extract_root)
            return _success_message_with_paths(
                f"已解压 {len(extracted)} 个文件到：`{abs_root}`",
                [abs_root],
                downgrade_note,
            )

        if os.path.exists(output):
            with open(output, "rb") as f:
                existed_file_sha = hashlib.sha256(f.read()).hexdigest()
            response_content_sha = hashlib.sha256(response.content).hexdigest()
            if existed_file_sha == response_content_sha:
                abs_output = os.path.abspath(output)
                return _success_message_with_paths(
                    f"文件已存在：`{abs_output}`",
                    [abs_output],
                    downgrade_note,
                )
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
        abs_output = os.path.abspath(output)
        if "文件名重复" in return_message:
            return _success_message_with_paths(return_message, [abs_output], downgrade_note)
        return _success_message_with_paths(
            f"文件已保存到：`{abs_output}`",
            [abs_output],
            downgrade_note,
        )
    except Exception as e:
        return f"获取文件失败：{str(e)}"

def _download_success_info(message: str) -> tuple[bool, int, bool, List[str]]:
    """解析 get_file 返回：(是否成功, 文件数, 是否为 zip 解压, 路径列表)。"""
    paths = _paths_from_success_message(message)
    if not message:
        return False, 0, False, paths

    m = re.search(r"已解压\s*(\d+)\s*个文件", message)
    if m:
        return True, int(m.group(1)), True, paths
    if any(
        key in message
        for key in (
            "文件已保存到",
            "文件名重复，已自动重命名",
            "文件保存路径已自动修正",
            "文件已存在",
        )
    ):
        return True, 1, False, paths
    return False, 0, False, paths


def _format_download_summary(
    headline: str,
    total_files: int,
    paths: List[str],
) -> str:
    msg = f"{headline}，共计{total_files}个文件"
    ordered: List[str] = []
    seen: set = set()
    for p in paths:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            ordered.append(ap)
    if ordered:
        msg += "\n" + "\n".join(f"- `{p}`" for p in ordered)
    return msg


def _should_mark_web_file(file: dict) -> bool:
    """有原文链接但支持 download/file 的类型（如公众号）不应标记为纯网络文件。"""
    if str(file.get("类型", "") or "").strip() == "公众号":
        return False
    return bool(file.get("网络连接", ""))


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
    total_saved_files = 0
    had_zip_extract = False
    all_saved_paths: List[str] = []

    if method_name == "official_account":
        default_types = ["txt"]
    elif method_name == "foreign_opinion":
        default_types = ["html"]
    elif method_name == "pamirs_summary":
        default_types = ["original"]
    else:
        default_types = ["markdown"]
    effective_types = download_types or default_types

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
        for download_type in effective_types:
            return_message = get_file(
                file["类型ID"],
                file["类型"],
                output=None,
                output_dir=target_dir,
                title=file.get("标题"),
                download_type=download_type,
            )
            ok, n_files, extracted, paths = _download_success_info(return_message)
            if ok:
                total_saved_files += n_files
                if extracted:
                    had_zip_extract = True
                for p in paths:
                    ap = os.path.abspath(p)
                    if ap not in all_saved_paths:
                        all_saved_paths.append(ap)
            else:
                failed_message.append({
                    "title": file["标题"],
                    "message": return_message,
                    "download_type": download_type,
                    "web_file": _should_mark_web_file(file),
                })
    if len(failed_message) == len(unique_files) and unique_files:
        return_message = "\n".join([f"- {x['title']}({x['download_type']})：{'是网络文件' if x['web_file'] else x['message']}" for x in failed_message])
    elif len(failed_message) > 0:
        prefix = "部分文件下载成功并解压缩" if had_zip_extract else "部分文件下载成功"
        return_message = _format_download_summary(prefix, total_saved_files, all_saved_paths)
        return_message += "; 其中有下载失败的文件：\n" + "\n".join([f"- {x['title']}({x['download_type']})：{'是网络文件' if x['web_file'] else x['message']}" for x in failed_message])
    else:
        if had_zip_extract:
            return_message = _format_download_summary(
                "文件全部下载成功并解压缩", total_saved_files, all_saved_paths
            )
        else:
            return_message = _format_download_summary(
                "文件全部下载成功", total_saved_files, all_saved_paths
            )
    return path_warn + return_message

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="RAG 文件检索命令行：按查询语句检索相关文件。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-id", "--file-id", default="", help="文件ID")
    parser.add_argument("-type", "--file-type", default="", help="文件类型")
    parser.add_argument("-od", "--output-dir", default="", help="输出目录路径")
    parser.add_argument(
        "-dt",
        "--download-type",
        default="markdown",
        help=(
            "下载类型：A 股研报/公告等为 pdf|markdown；外资研报为 pdf|markdown|zh_pdf|zh_markdown（或 1/2/3/4）；"
            "外资独立观点为 html|zh；财报日历仅原文件（忽略本参数）"
        ),
    )

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
        output_dir=args.output_dir,
        download_type=args.download_type,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors='ignore')
    main()