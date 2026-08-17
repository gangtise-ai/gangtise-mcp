import os
import re
from typing import Dict, List, Any, Optional, Tuple
import hashlib
import pandas as pd
import datetime
import time
import requests
import json

FILE_DEFAULT_LIMIT = {
    "wechat_message": 1000,
    "private_meeting": 100,
    "private_record": 100,
    "private_cloud": 100,
}

from authorization import (
    get_authorization_headers,
    get_authorization_token,
    get_headers_extra,
    gangtise_domain,
    invalidate_authorization,
)

GTS_SAVE_FILE = os.getenv("GTS_SAVE_FILE", True)
GTS_SAVE_EXTENSION = os.getenv("GTS_SAVE_EXTENSION", "md")

GANGTISE_VAULT_DOMAIN = gangtise_domain("GANGTISE_VAULT_DOMAIN", "https://openapi.gangtise.com/application/open-vault")
WECHAT_GROUP_MSG_LIST_URL = GANGTISE_VAULT_DOMAIN + "/wechatgroupmsg/list"
WECHAT_GROUP_CHATROOM_URL = GANGTISE_VAULT_DOMAIN + "/wechatgroupmsg/chatroomId"
GET_POOL_LIST_URL = GANGTISE_VAULT_DOMAIN + "/stock-pool/getPoolList"
GET_STOCK_LIST_URL = GANGTISE_VAULT_DOMAIN + "/stock-pool/getStockList"
MY_CONFERENCE_LIST_URL = GANGTISE_VAULT_DOMAIN + "/my-conference/getList"
MY_CONFERENCE_DOWNLOAD_URL = GANGTISE_VAULT_DOMAIN + "/my-conference/download/file"
RECORD_LIST_URL = GANGTISE_VAULT_DOMAIN + "/record/getList"
RECORD_DOWNLOAD_URL = GANGTISE_VAULT_DOMAIN + "/record/download/file"
DRIVE_LIST_URL = GANGTISE_VAULT_DOMAIN + "/drive/getList"
DRIVE_DOWNLOAD_URL = GANGTISE_VAULT_DOMAIN + "/drive/download/file"
WORK_PATH = os.getenv("WORK_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workspace"))
if not os.path.exists(WORK_PATH):
    os.makedirs(WORK_PATH, exist_ok=True)

usage_dir = os.path.join(WORK_PATH, ".usage")
if not os.path.exists(usage_dir):
    os.makedirs(usage_dir, exist_ok=True)

file_dir = os.path.join(WORK_PATH, "files")
if not os.path.exists(file_dir):
    os.makedirs(file_dir, exist_ok=True)

def remove_html_tags(text):
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

def data_to_md(data: pd.DataFrame, range: List[int]=None, max_cell_length: int=None):
    data_copy = data.copy()
    if "metadata" in data_copy.columns:
        data_copy = data_copy.drop(columns=["metadata"])
    content = "| " + " | ".join(data_copy.columns) + " |\n"
    content += "| " + " | ".join(["-" for _ in data_copy.columns]) + " |\n"
    omitted = False
    for i, row in enumerate(data_copy.to_dict(orient="records")):
        if range:
            if i in range:
                if max_cell_length:
                    content += "| " + " | ".join([re.sub(r"\s+", " ", str(row[key]).replace("\n"," ")).replace("|", "")[:max_cell_length]+"..." if len(re.sub(r"\s+", " ", str(row[key])).replace("|", "")) > max_cell_length else re.sub(r"\s+", " ", str(row[key])).replace("|", "") for key in row.keys()]) + " |\n"
                else:
                    content += "| " + " | ".join([re.sub(r"\s+", " ", str(row[key]).replace("\n"," ")).replace("|", "") for key in row.keys()]) + " |\n"
            elif not omitted:
                content += "| ... |\n"
                omitted = True
        else:
            if max_cell_length:
                content += "| " + " | ".join([re.sub(r"\s+", " ", str(row[key]).replace("\n"," ")).replace("|", "")[:max_cell_length]+"..." if len(re.sub(r"\s+", " ", str(row[key])).replace("|", "")) > max_cell_length else re.sub(r"\s+", " ", str(row[key])).replace("|", "") for key in row.keys()]) + " |\n"
            else:
                content += "| " + " | ".join([re.sub(r"\s+", " ", str(row[key]).replace("\n"," ")).replace("|", "") for key in row.keys()]) + " |\n"
    content = content[:-1]
    return content.strip()

def add_usages(usages_list: List[Dict[str, Any]]):
    usages = {}
    for usages_item in usages_list:
        if len(usages_item) == 0:
            continue
        for k,v in usages_item.items():
            if k not in usages:
                usages[k] = v
            else:
                usages[k] = usages[k] + v
    return usages

_FILE_SKIP_IN_BODY = frozenset({"标题", "文件时间", "消息时间", "文件内容", "类型", "类型ID", "中文标题"})
_FILE_MULTILINE = frozenset({"摘要", "中文摘要", "正文", "消息全文"})

def _save_file_enabled() -> bool:
    v = GTS_SAVE_FILE
    if v is True:
        return True
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)

def _format_file_record_text(file: dict, method_name: str, module_name: str, output: Optional[str] = None) -> str:
    lines: List[str] = []
    if file.get("标题"):
        lines.append(f"标题：{file['标题']}")
    if file.get("中文标题"):
        lines.append(f"中文标题：{file['中文标题']}")
    if file.get("消息时间"):
        lines.append(f"消息时间：{file['消息时间']}")
    if file.get("文件内容"):
        if file.get("文件内容", {}).get("status", "failed") == "success":
            file_path = _alloc_workspace_path(method_name, module_name, GTS_SAVE_EXTENSION, output, filename=file.get("文件内容", {}).get("filename", None))
            if file_path[0] and file_path[1] == "exist":
                existed_file_sha = hashlib.sha256(open(file_path[0], "rb").read()).hexdigest()
                response_content_sha = hashlib.sha256(file.get("文件内容").get("file_bytes")).hexdigest()
                if existed_file_sha == response_content_sha:
                    lines.append(f"文件已存在：`{os.path.abspath(file_path[0])}`")
                else:
                    iteration = 1
                    output_extension = output.split(".")[-1]
                    output_name = output.split(".")[0]
                    while os.path.exists(output):
                        output = os.path.join(os.path.dirname(output), f"{output_name}({iteration}).{output_extension}")
                        iteration += 1
                    with open(output, "wb") as f:
                        f.write(file.get("文件内容").get("file_bytes"))
                    lines.append(f"文件名重复，已自动重命名并下载到 `{os.path.abspath(output)}`")
            elif file_path[0]:
                with open(file_path[0], "wb") as f:
                    f.write(file.get("文件内容").get("file_bytes"))
                lines.append(f"文件已下载到 `{os.path.abspath(file_path[0])}`")
            else:
                lines.append(f"文件下载失败：{file_path[1]}")
        else:
            lines.append(f"文件下载失败：{file.get('文件内容').get('message')}")
    for key, value in file.items():
        if key in _FILE_SKIP_IN_BODY or not value:
            continue
        if key in _FILE_MULTILINE:
            lines.append(f"{key}：\"\"\"\n{value}\n\"\"\"")
        else:
            lines.append(f"{key}：{value}")
    if file.get("类型"):
        lines.append(f"file-type：{file['类型']}")
    if file.get("类型ID") is not None and str(file.get("类型ID", "")).strip() != "":
        lines.append(f"file-id：{file['类型ID']}")
    return "\n".join(lines)

def _alloc_workspace_path(
    method_name: str,
    module_name: str,
    extension: str,
    output: Optional[str] = None,
    filename: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    if output:
        if os.path.exists(output):
            return output, "exist"
        return output, None
    process_dir = os.path.join(WORK_PATH, method_name)
    os.makedirs(process_dir, exist_ok=True)
    if filename:
        process_path = os.path.join(process_dir, filename)
        if os.path.exists(process_path):
            return process_path, "exist"
        return process_path, None
    now = datetime.datetime.now().strftime("%H%M%S")
    process_path = os.path.join(process_dir, f"{module_name}_{now}.{extension}")
    max_retries = 10
    while os.path.exists(process_path) and max_retries > 0:
        time.sleep(1)
        now = datetime.datetime.now().strftime("%H%M%S")
        process_path = os.path.join(process_dir, f"{module_name}_{now}.{extension}")
        max_retries -= 1
    if max_retries == 0:
        return None, "错误信息：文件存储系统繁忙，请稍后再试"
    return process_path, None

def format_response(
    response: dict,
    method_name: str,
    output: Optional[str] = None,
    additional_message: str = "",
):
    # 保存 usage
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    now = datetime.datetime.now().strftime("%H%M%S")
    usage_path = os.path.join(usage_dir, f"{today}.json")
    if response.get("usage", None):
        if os.path.exists(usage_path):
            with open(usage_path, "r", encoding="utf-8") as f:
                usage = json.load(f)
            if now in usage:
                now_usage = add_usages([response["usage"], usage[now]])
            else:
                now_usage = response["usage"]
            usage.update({now: now_usage})
        else:
            usage = {now: response["usage"]}
        with open(usage_path, "w", encoding="utf-8") as f:
            json.dump(usage, f, ensure_ascii=False)

    return_message = response.get("message", "") + "\n\n" if response.get("message", "") else ""
    method_name_map = {
        "stockpool": "自选股股票池",
        "wechat_message": "微信群消息",
        "private_meeting": "我的会议",
        "private_record": "录音速记",
        "private_cloud": "AI云盘",
    }
    module_name_map = {
        "stockpool_pools": "股票池列表",
        "stockpool_stocks": "证券明细",
        "wechat_chatroom": "微信群列表",
        "wechat_message": "群消息",
        "private_meeting_list": "会议列表",
        "private_meeting_content": "会议内容",
        "private_record_list": "录音列表",
        "private_record_content": "录音内容",
        "private_cloud_list": "云盘文件列表",
        "private_cloud_content": "云盘文件内容",
    }

    if response["state"] == "success":
        for item in response["data"]:
            module_name = item["module"]
            data = item["data"]
            item_type = item.get("type", "files")
            label = method_name_map.get(method_name, method_name)
            sub = module_name_map.get(module_name, module_name)

            if item_type == "data":
                process_path, err = _alloc_workspace_path(method_name, module_name, "csv", output)
                if err:
                    return err
                df = pd.DataFrame(data)
                df.to_csv(process_path, index=False)
                n = len(df)
                if n <= 6:
                    sample_range = list(range(n))
                else:
                    sample_range = [0, 1, 2, n - 3, n - 2, n - 1]
                sample_data = data_to_md(df, range=sample_range)
                return_message += (
                    f"### {label} · {sub} 已保存到 csv：\n`"
                    + os.path.abspath(process_path)
                    + f"`\n\n#### 样例数据:\n{sample_data}\n\n共 {n} 条\n\n---\n\n"
                )
                continue

            # type == "files"：群消息等，对齐 gangtise-file 的落盘与内联展示
            if _save_file_enabled():
                process_path, err = _alloc_workspace_path(
                    method_name, module_name, GTS_SAVE_EXTENSION, output
                )
                if err:
                    return err
                if GTS_SAVE_EXTENSION == "md":
                    with open(process_path, "w", encoding="utf-8") as f:
                        for i, file in enumerate(data):
                            f.write(_format_file_record_text(file, method_name, module_name, output))
                            if i < len(data) - 1:
                                f.write("\n\n---\n\n")
                elif GTS_SAVE_EXTENSION == "json":
                    with open(process_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                else:
                    with open(process_path, "w", encoding="utf-8") as f:
                        for i, file in enumerate(data):
                            f.write(_format_file_record_text(file, method_name, module_name, output))
                            if i < len(data) - 1:
                                f.write("\n\n---\n\n")
                # 落盘时正文最多展示 5 个 file 作为示例，完整结果见文件
                preview_n = 5
                preview = data[:preview_n]
                sample_blocks = [
                    _format_file_record_text(file, method_name, module_name, output)
                    for file in preview
                ]
                sample_data = "\n\n---\n\n".join(sample_blocks)
                total = len(data)
                count_line = (
                    f"仅展示前 {preview_n} 条示例，共 {total} 条；完整结果见文件"
                    if total > preview_n
                    else f"查询结果共计 {total} 条"
                )
                return_message += (
                    f"### {label} 查询结果:\n\n---\n\n{sample_data}\n\n"
                    f"<!-- 所有查询结果已保存到 {GTS_SAVE_EXTENSION}：`"
                    + os.path.abspath(process_path)
                    + f"` -->\n\n"
                    f"{count_line}\n\n---\n\n"
                )
            else:
                sample_blocks = [
                    _format_file_record_text(file, method_name, module_name, output)
                    for file in data
                ]
                sample_data = "\n\n---\n\n".join(sample_blocks)
                return_message += (
                    f"### {label} 查询结果:\n\n---\n\n{sample_data}\n\n"
                    f"查询结果共计 {len(data)} 条\n\n---\n\n"
                )
    else:
        return_message = "调用 Gangtise Open API 失败，错误信息：" + response.get("message", "")

    if additional_message:
        return_message += "\n" + additional_message
    if return_message.endswith("---\n\n"):
        return_message = return_message[:-6].strip()
    return return_message

def is_code_arg(raw: str) -> bool:
    """整段参数仅含字母、数字与逗号时视为 ID/编码，否则走 search。"""
    s = (raw or "").strip()
    return bool(s) and re.fullmatch(r"[a-zA-Z,，0-9]+", s) is not None

def load_pool_ids_from_file(path: str) -> List[str]:
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"股票池文件不存在: {path}")
    df = pd.read_csv(full_path)
    for col in ("pool_id", "poolId", "poolIdList"):
        if col in df.columns:
            return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]
    raise ValueError("股票池文件须包含 pool_id 或 poolId 列")

def load_securities_from_file(path: str) -> List[str]:
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"证券文件不存在: {path}")
    df = pd.read_csv(full_path)
    if "security_abbr" in df.columns:
        return [str(x) for x in df["security_abbr"].dropna().tolist()]
    if "security_code" in df.columns:
        return [str(x) for x in df["security_code"].dropna().tolist()]
    raise ValueError("证券文件必须包含 security_abbr 或 security_code 列")

def match_best(item: str, candidates, threshold: float = 0.6):
    """
    candidates: List[str] 时返回匹配的字符串，Dict[str, Any] 时返回匹配 key 的 {k: v}。
    无匹配返回 None。
    """
    from difflib import SequenceMatcher

    if not item or not candidates:
        return None
    if item in candidates:
        return item

    is_dict = isinstance(candidates, dict)
    keys = list(candidates.keys()) if is_dict else candidates

    if item in keys:
        return {item: candidates[item]} if is_dict else item

    best_score = 0.0
    best_key = None

    for key in keys:
        if item in key or key in item:
            overlap = min(len(item), len(key))
            score = overlap / max(len(item), len(key))
            score = max(score, 0.8)
        else:
            score = SequenceMatcher(None, item, key).ratio()

        if score > best_score:
            best_score = score
            best_key = key

    if best_score >= threshold and best_key is not None:
        return {best_key: candidates[best_key]} if is_dict else best_key
    return None

PRIVATE_SKILL_VERSION = "1.0.0"
OPENAPI_SKILL_VERSION = "1.7.0"
SKILL_CHECK_URL = "https://open.gangtise.com/application/skills-backend/version?skill=openapi"

def check_version(large_version: bool = True):
    response = requests.get(SKILL_CHECK_URL)
    if response.status_code == 200 and large_version:
        return response.json()["state"] == "success" and response.json()["version"].split(".")[0] == OPENAPI_SKILL_VERSION.split(".")[0] and response.json()["version"].split(".")[1] == OPENAPI_SKILL_VERSION.split(".")[1]
    elif response.status_code == 200 and not large_version:
        return response.json()["state"] == "success" and response.json()["version"] == OPENAPI_SKILL_VERSION
    else:
        return False

if __name__ == "__main__":
    print("检查 gangtise-private 相关配置")
    if not get_authorization_token():
        print("  无法检测到 gangtise 密钥环境变量或授权文件，gangtise-private 无法正常工作")
    else:
        print("  已检测到 gangtise 授权，gangtise-private 可以正常工作")
    if _save_file_enabled():
        print(f"  GTS_SAVE_FILE 已启用，群消息等将保存为 {GTS_SAVE_EXTENSION}（股票池 CSV 始终落盘）")
    else:
        print("  GTS_SAVE_FILE 未启用：群消息仅在回复中展示；设置 GTS_SAVE_FILE=True 可落盘 md/json")
    if check_version(large_version=False):
        print("  gangtise-private 依赖的 OpenAPI skills 版本检查通过")
    else:
        print("  建议检查 OpenAPI skills 版本更新")
    print(f"  工作目录: {WORK_PATH}")