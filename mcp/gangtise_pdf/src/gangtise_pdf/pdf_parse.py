"""高精度 PDF 解析（OpenAPI）：一个工具两个 action — submit / result。"""
from __future__ import annotations

import os
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import requests
from authorization import authorized_request, get_authorization_headers, get_authorization_token

GANGTISE_TOOL_DOMAIN = os.getenv(
    "GANGTISE_TOOL_DOMAIN",
    "https://openapi.gangtise.com/application/open-tool",
)
FILE_PARSE_SUBMIT_URL = f"{GANGTISE_TOOL_DOMAIN}/file-parse/submit"
FILE_PARSE_RESULT_URL = f"{GANGTISE_TOOL_DOMAIN}/file-parse/result"

RESULT_GENERATING_CODE = "140001"
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PAGES = 500

# 经验预估：启动约 5s，之后约 1s/页
ESTIMATE_STARTUP_SECONDS = int(os.getenv("PDF_PARSE_ESTIMATE_STARTUP", "5"))
ESTIMATE_PER_PAGE_SECONDS = int(os.getenv("PDF_PARSE_ESTIMATE_PER_PAGE", "1"))

DEFAULT_POLL_INTERVAL = 10
DEFAULT_RESULT_TIMEOUT = 60  # result 单次调用默认轮询上限；未完成可再次 result

WORK_PATH = os.getenv("WORK_PATH") or os.getenv("GTS_WORK_PATH")


def _workspace_files_dir() -> Path:
    if WORK_PATH:
        base = Path(WORK_PATH)
    else:
        base = Path(os.getenv("HOME", "/tmp")) / "gangtise"
    files = base / "files"
    files.mkdir(parents=True, exist_ok=True)
    return files


def estimate_parse_seconds(pages: int) -> int:
    pages = max(0, int(pages or 0))
    return ESTIMATE_STARTUP_SECONDS + pages * ESTIMATE_PER_PAGE_SECONDS


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"约 {seconds} 秒"
    m, s = divmod(seconds, 60)
    if s == 0:
        return f"约 {m} 分钟"
    return f"约 {m} 分 {s} 秒"


def pdf_page_count(pdf_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


def _default_output_dir(pdf_path: Optional[Path] = None, task_id: Optional[str] = None) -> Path:
    if pdf_path is not None:
        name = f"{pdf_path.stem}_parsed"
    elif task_id:
        name = f"task_{task_id}_parsed"
    else:
        name = "pdf_parsed"
    return _workspace_files_dir() / name


def _submit(pdf_path: Path, headers: dict) -> str:
    with open(pdf_path, "rb") as f:
        files = {"file": (pdf_path.name, f, "application/pdf")}
        resp = authorized_request("POST", FILE_PARSE_SUBMIT_URL, headers=headers, files=files, timeout=120)
    try:
        body = resp.json()
    except Exception as e:
        raise RuntimeError(f"提交失败 HTTP {resp.status_code}: {resp.text[:500]}") from e
    if resp.status_code != 200 or str(body.get("code")) != "000000" or not body.get("status"):
        raise RuntimeError(f"提交失败: {body.get('msg') or body}")
    task_id = (body.get("data") or {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"提交成功但缺少 taskId: {body}")
    return str(task_id)


def _fetch_result_once(task_id: str, headers: dict) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """返回 (zip_bytes|None, status, message)。status: ready|generating|error。"""
    json_headers = {**headers, "Content-Type": "application/json"}
    resp = authorized_request("POST", 
        FILE_PARSE_RESULT_URL,
        headers=json_headers,
        json={"taskId": task_id},
        timeout=120,
    )
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/zip" in content_type or (
        resp.status_code == 200 and resp.content[:2] == b"PK" and "json" not in content_type
    ):
        return resp.content, "ready", None
    if resp.status_code == 200 and resp.content[:2] == b"PK":
        return resp.content, "ready", None

    try:
        body = resp.json()
    except Exception:
        return None, "error", f"HTTP {resp.status_code}: {resp.text[:300]}"

    code = str(body.get("code", ""))
    if code == RESULT_GENERATING_CODE:
        return None, "generating", body.get("msg") or "结果生成中"
    if code == "000000" and body.get("status"):
        data = body.get("data") or {}
        if isinstance(data, dict) and data.get("content"):
            import base64

            return base64.b64decode(data["content"]), "ready", None
        return None, "generating", body.get("msg") or "结果尚未就绪"
    return None, "error", f"code={code}, msg={body.get('msg') or body}"


def _unpack_zip(zip_bytes: bytes, output_dir: Path, *, keep_zip: bool, zip_stem: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if keep_zip:
        (output_dir / f"{zip_stem}_parse.zip").write_bytes(zip_bytes)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        zf.extractall(output_dir)
    md_path = output_dir / "file.md"
    if not md_path.exists():
        candidates = list(output_dir.rglob("file.md"))
        if not candidates:
            raise RuntimeError(f"ZIP 已解压到 {output_dir}，但未找到 file.md")
        md_path = candidates[0]
    return md_path


def _action_submit(
    pdf_path: str,
    output_dir: Optional[str] = None,
) -> str:
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        return f"错误：文件不存在：{path}"
    if path.suffix.lower() != ".pdf":
        return f"错误：仅支持 PDF 文件：{path}"

    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return f"错误：文件超过 100 MB 限制（当前 {size / (1024 * 1024):.1f} MB）"

    try:
        pages = pdf_page_count(path)
    except Exception as e:
        return f"错误：无法读取 PDF 页数：{e}"
    if pages > MAX_PAGES:
        return f"错误：页数超过 500 页限制（当前 {pages} 页）"

    headers = get_authorization_headers()
    if not get_authorization_token():
        return "错误：未配置 Authorization，请设置 GTS_AUTHORIZATION 或 AK/SK"

    out = (
        Path(output_dir).expanduser().resolve()
        if output_dir and not (os.name != "nt" and ("\\" in output_dir or (len(output_dir) >= 2 and output_dir[1] == ":")))
        else _default_output_dir(path)
    )
    out.mkdir(parents=True, exist_ok=True)

    try:
        task_id = _submit(path, headers)
    except Exception as e:
        return f"提交失败：{e}"

    (out / "task_id.txt").write_text(task_id + "\n", encoding="utf-8")
    est = estimate_parse_seconds(pages)
    cost = pages * 0.8
    return (
        f"已提交 PDF 解析任务\n"
        f"- taskId: `{task_id}`\n"
        f"- 文件: `{path}`\n"
        f"- 页数: {pages}\n"
        f"- 预估积分: {cost:.1f}（0.8/页）\n"
        f"- 预估耗时: {format_duration(est)}（启动约 {ESTIMATE_STARTUP_SECONDS}s + 约 {ESTIMATE_PER_PAGE_SECONDS}s/页）\n"
        f"- 预估秒数: {est}\n"
        f"- taskId 已写入: `{out / 'task_id.txt'}`\n\n"
        f"请稍后调用同一工具 action=`result`，传入 task_id=`{task_id}` 下载结果。"
        f"若仍返回「生成中」，按预估时间再试，勿重复 submit（会再次扣积分）。"
    )


def _action_result(
    task_id: str,
    output_dir: Optional[str] = None,
    pdf_path: Optional[str] = None,
    poll_interval: Optional[int] = None,
    timeout: Optional[int] = None,
    keep_zip: bool = False,
) -> str:
    tid = (task_id or "").strip()
    if not tid:
        return "错误：action=result 时必须提供 task_id"

    headers = get_authorization_headers()
    if not get_authorization_token():
        return "错误：未配置 Authorization，请设置 GTS_AUTHORIZATION 或 AK/SK"

    pdf = Path(pdf_path).expanduser().resolve() if pdf_path else None
    use_client_dir = bool(
        output_dir
        and not (
            os.name != "nt"
            and ("\\" in output_dir or (len(output_dir) >= 2 and output_dir[1] == ":"))
        )
    )
    out = (
        Path(output_dir).expanduser().resolve()
        if use_client_dir
        else _default_output_dir(pdf if pdf and pdf.is_file() else None, tid)
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "task_id.txt").write_text(tid + "\n", encoding="utf-8")

    interval = int(poll_interval) if poll_interval is not None else DEFAULT_POLL_INTERVAL
    wait = int(timeout) if timeout is not None else DEFAULT_RESULT_TIMEOUT
    interval = max(3, interval)
    wait = max(interval, wait)

    deadline = time.monotonic() + wait
    last_msg = "结果生成中"
    while True:
        try:
            zip_bytes, status, msg = _fetch_result_once(tid, headers)
        except Exception as e:
            return f"获取结果失败：{e}\ntaskId=`{tid}`"

        if status == "ready" and zip_bytes is not None:
            stem = pdf.stem if pdf and pdf.is_file() else f"task_{tid}"
            try:
                md_path = _unpack_zip(zip_bytes, out, keep_zip=bool(keep_zip), zip_stem=stem)
            except Exception as e:
                return f"解压失败：{e}\ntaskId=`{tid}`\noutput_dir=`{out}`"
            return (
                f"解析完成\n"
                f"- taskId: `{tid}`\n"
                f"- output_dir:\n`{out}`\n"
                f"- markdown:\n`{md_path}`\n"
                f"- images: `{out / 'images'}`（若 ZIP 含图片目录）"
            )

        if status == "error":
            return f"获取结果失败：{msg}\ntaskId=`{tid}`"

        last_msg = msg or last_msg
        if time.monotonic() + interval > deadline:
            break
        time.sleep(interval)

    return (
        f"结果仍在生成中（{last_msg}）\n"
        f"- taskId: `{tid}`\n"
        f"- 本次已等待约 {wait}s\n"
        f"- 请稍后再调用 action=`result`（同一 task_id，不重复扣费）\n"
        f"- output_dir: `{out}`"
    )


def pdf_parse(
    action: str,
    pdf_path: Optional[str] = None,
    task_id: Optional[str] = None,
    output_dir: Optional[str] = None,
    poll_interval: Optional[int] = None,
    timeout: Optional[int] = None,
    keep_zip: bool = False,
) -> str:
    """
    高精度 PDF → Markdown（含图片）异步解析。

    action=submit：上传 PDF，返回 taskId 与预估耗时（约 5s + 1s/页）。
    action=result：按 taskId 下载并解压结果；未完成时提示稍后重试。
    """
    act = (action or "").strip().lower()
    if act in ("submit", "parse", "upload"):
        if not pdf_path:
            return "错误：action=submit 时必须提供 pdf_path（PDF 绝对路径）"
        return _action_submit(pdf_path, output_dir=output_dir)
    if act in ("result", "download", "fetch", "get"):
        return _action_result(
            task_id or "",
            output_dir=output_dir,
            pdf_path=pdf_path,
            poll_interval=poll_interval,
            timeout=timeout,
            keep_zip=keep_zip,
        )
    return (
        f"错误：未知 action=`{action}`。可选：submit（提交解析）、result（下载结果）。"
    )
