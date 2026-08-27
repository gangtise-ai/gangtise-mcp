"""临时文件上传华为云 OBS（超大 MCP 附件外置 / MCP_ATTACH_OBS_ALWAYS 全量外置）。

环境变量：
  OBS_ACCESS_KEY / OBS_SECRET_KEY / OBS_ENDPOINT / OBS_BUCKET（静态）/ OBS_PATH
  OBS_EXPIRE_DAYS — 对象存活天数，默认 1（华为云 PutObjectHeader.expires）

对象上传时设置 expires（天），到期自动删除；返回公网路径：
  https://{OBS_BUCKET}.{OBS_ENDPOINT_HOST}/{object_key}

说明：该 URL 为虚拟主机风格公有读路径，桶策略或对象 ACL 需允许匿名 GET
（或经 CDN 回源）；否则客户端无法直接下载。
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import quote


def _expire_days() -> int:
    raw = (os.getenv("OBS_EXPIRE_DAYS") or "1").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 1
    return max(1, days)


# 模块导入时解析一次；进程内改环境变量后需重启才生效（与其它 OBS_* 一致）
EXPIRE_DAYS = _expire_days()

_SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def _env() -> Tuple[str, str, str, str, str]:
    return (
        os.getenv("OBS_ACCESS_KEY", "").strip(),
        os.getenv("OBS_SECRET_KEY", "").strip(),
        os.getenv("OBS_ENDPOINT", "").strip(),
        os.getenv("OBS_BUCKET", "").strip(),
        os.getenv("OBS_PATH", "").strip(),
    )


def is_configured() -> bool:
    """凭证与 bucket/endpoint 齐全即视为可用；OBS_PATH 可为空（落到桶根）。"""
    ak, sk, endpoint, bucket, _path = _env()
    return bool(ak and sk and endpoint and bucket)


def _endpoint_host(endpoint: str) -> str:
    host = (endpoint or "").strip()
    for prefix in ("https://", "http://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
            break
    return host.rstrip("/")


def _normalize_prefix(prefix: str) -> str:
    p = (prefix or "").strip().replace("\\", "/").lstrip("/")
    if p and not p.endswith("/"):
        p += "/"
    return p


def _safe_filename(name: str) -> str:
    base = os.path.basename(name) or "attachment.bin"
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "attachment.bin"
    return cleaned[:180]


def build_object_key(filename: str, obs_path: str = "") -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{_normalize_prefix(obs_path)}{day}/{uuid.uuid4().hex}_{_safe_filename(filename)}"


def public_url(object_key: str, *, bucket: str = "", endpoint: str = "") -> str:
    """https://{OBS_BUCKET}.{OBS_ENDPOINT}/..."""
    if not bucket or not endpoint:
        _ak, _sk, endpoint_env, bucket_env, _path = _env()
        bucket = bucket or bucket_env
        endpoint = endpoint or endpoint_env
    host = _endpoint_host(endpoint)
    key = object_key.lstrip("/")
    encoded = "/".join(quote(seg, safe="") for seg in key.split("/") if seg != "")
    return f"https://{bucket}.{host}/{encoded}"


def upload_bytes(
    data: bytes,
    filename: str,
    *,
    content_type: str = "application/octet-stream",
) -> str:
    """上传字节并返回公网 URL；未配置或失败时抛异常。"""
    ak, sk, endpoint, bucket, obs_path = _env()
    if not (ak and sk and endpoint and bucket):
        raise RuntimeError(
            "OBS 未配置：需要 OBS_ACCESS_KEY / OBS_SECRET_KEY / OBS_ENDPOINT / OBS_BUCKET"
        )
    try:
        from obs import ObsClient, PutObjectHeader
    except ImportError as e:
        raise RuntimeError("缺少依赖 esdk-obs-python，请先安装") from e

    object_key = build_object_key(filename, obs_path)
    header = PutObjectHeader(contentType=content_type or "application/octet-stream")
    header.expires = _expire_days()

    server = endpoint if "://" in endpoint else f"https://{endpoint}"
    client = ObsClient(access_key_id=ak, secret_access_key=sk, server=server)
    try:
        resp = client.putContent(bucket, object_key, data, headers=header)
        if getattr(resp, "status", 500) >= 300:
            raise RuntimeError(
                f"OBS 上传失败 status={resp.status} "
                f"code={getattr(resp, 'errorCode', '')} "
                f"msg={getattr(resp, 'errorMessage', '')}"
            )
    finally:
        try:
            client.close()
        except Exception:
            pass

    return public_url(object_key, bucket=bucket, endpoint=endpoint)


def try_upload_bytes(
    data: bytes,
    filename: str,
    *,
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """配置齐全则上传并返回 URL；未配置返回 None；失败抛异常。"""
    if not is_configured():
        return None
    return upload_bytes(data, filename, content_type=content_type)
