import os
import re
from typing import Dict, List, Any, Optional
# import logging
# from logging.handlers import TimedRotatingFileHandler
import pandas as pd
import datetime
import time
import json
import requests

# 通过 ak/sk 获取 临时 authorization

from authorization import (
    get_authorization_headers,
    get_authorization_token,
    get_headers_extra,
    gangtise_domain,
    invalidate_authorization,
)

GTS_SAVE_FILE = os.getenv("GTS_SAVE_FILE", True)
GTS_SAVE_EXTENSION = os.getenv("GTS_SAVE_EXTENSION", "md")

GANGTISE_DATA_DOMAIN = gangtise_domain("GANGTISE_DATA_DOMAIN", "https://openapi.gangtise.com/application/open-data")
GANGTISE_INSIGHT_DOMAIN = gangtise_domain("GANGTISE_INSIGHT_DOMAIN", "https://openapi.gangtise.com/application/open-insight")
RAG_URL = GANGTISE_DATA_DOMAIN + "/ai/search/knowledge_base"
FILE_URL = GANGTISE_DATA_DOMAIN + "/ai/resource/download"
SUMMARY_DOWNLOAD_URL = GANGTISE_INSIGHT_DOMAIN + "/summary/v2/download/file"
COMPANY_ANNOUNCEMENT_DOWNLOAD_URL = GANGTISE_INSIGHT_DOMAIN + "/announcement/download/file"
REPORT_DOWNLOAD_URL = GANGTISE_INSIGHT_DOMAIN + "/broker-report/download/file"
FOREIGN_REPORT_DOWNLOAD_URL = GANGTISE_INSIGHT_DOMAIN + "/foreign-report/download/file"
WORK_PATH = os.getenv("WORK_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workspace"))
if not os.path.exists(WORK_PATH):
    os.makedirs(WORK_PATH, exist_ok=True)

usage_dir = os.path.join(WORK_PATH, ".usage")
if not os.path.exists(usage_dir):
    os.makedirs(usage_dir, exist_ok=True)

file_dir = os.path.join(WORK_PATH, "files")
if not os.path.exists(file_dir):
    os.makedirs(file_dir, exist_ok=True)

FILE_TYPE_MAP = {
    "研究报告": 10,
    "外资研报": 11,
    "内部报告": 20,
    "AI云盘": 30,
    "首席观点": 40,
    "公司公告": 50,
    "港股公告": 51,
    "会议纪要": 60,
    "调研纪要": 70,
    "网络纪要": 80,
    "产业公众号": 90,
}

FILE_TYPE_MAP_REVERSE = {
    v: k for k, v in FILE_TYPE_MAP.items()
}

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

def format_file_time(value: Any) -> str:
    """将文件时间转为 yyyy-MM-dd HH:MM:SS；支持秒/毫秒时间戳，已是字符串则原样返回。"""
    if value is None:
        return ""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        if re.fullmatch(r"\d{10,13}", s):
            ts = int(s)
        else:
            return s
    elif isinstance(value, (int, float)):
        ts = int(value)
    else:
        return str(value).strip()

    if ts > 10_000_000_000:
        ts //= 1000
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return str(value)

def _normalize_kb_file_records(data: List[Dict[str, Any]]) -> None:
    for file in data:
        if "文件时间" in file:
            file["文件时间"] = format_file_time(file.get("文件时间"))

def format_response(response: dict, method_name: str, output: Optional[str] = None, additional_message: str = ""):
    
    # 保存usage
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

    # 保存结果
    return_message = ""
    method_name_map = {
        "block_component": "板块成分信息",
        "financial": "财务数据",
        "industry_indicator": "行业指标数据",
        "main_business": "主营业务数据",
        "quote": "行情数据",
        "security": "证券信息",
        "shareholder": "股东数据",
        "valuation": "估值数据",
        "rag": "知识库检索",
    }
    if response["state"] == "success":
        for item in response["data"]:
            module_name = item["module"]
            data = item["data"]
            _normalize_kb_file_records(data)
            if GTS_SAVE_FILE:
                if output:
                    process_path = output
                    if os.path.exists(process_path):
                        return_message = "错误信息：文件已存在"
                        return return_message
                else:
                    extension = GTS_SAVE_EXTENSION
                    process_dir = os.path.join(WORK_PATH, method_name)
                    if not os.path.exists(process_dir):
                        os.makedirs(process_dir, exist_ok=True)
                    now = datetime.datetime.now().strftime("%H%M%S")
                    process_path = os.path.join(process_dir, f"{module_name}_{now}.{extension}")
                    max_retries = 10
                    while os.path.exists(process_path) and max_retries > 0:
                        time.sleep(1)
                        now = datetime.datetime.now().strftime("%H%M%S")
                        process_path = os.path.join(process_dir, f"{module_name}_{now}.{extension}")
                        max_retries -= 1
                    if max_retries == 0:
                        return_message = "错误信息：文件存储系统繁忙，请稍后再试"
                        return return_message
                if GTS_SAVE_EXTENSION == "md":
                    with open(process_path, "w", encoding="utf-8") as f:
                        for i, file in enumerate(data):
                            f.write(f"### 查询结果 {i+1}\n\n")
                            f.write(f"标题：{file['标题']}\n")
                            f.write(f"文件时间：{file['文件时间']}\n")
                            f.write(f"内容：\"\"\"\n{file['摘要']}\n\"\"\"\n")
                            f.write(f"file-type：{file['类型']}\n")
                            f.write(f"file-id：{file['类型ID']}")
                            if i < len(data) - 1:
                                f.write("\n\n---\n\n")
                elif GTS_SAVE_EXTENSION == "json":
                    with open(process_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                # 落盘时正文最多展示 5 个 chunk 作为示例，完整结果见文件
                preview_n = 5
                preview_chunks = data[:preview_n]
                sample_data = ""
                for file in preview_chunks:
                    sample_data += f"标题：{file['标题']}\n"
                    sample_data += f"文件时间：{file['文件时间']}\n"
                    sample_data += f"内容：\"\"\"\n{file['摘要']}\n\"\"\"\n"
                    sample_data += f"file-type：{file['类型']}\n"
                    sample_data += f"file-id：{file['类型ID']}"
                    sample_data += "\n\n---\n\n"
                return_message += (
                    "### "
                    + method_name_map[method_name]
                    + " 查询结果:\n\n---\n\n"
                    + sample_data
                    + f"<!-- 所有查询结果已保存到{GTS_SAVE_EXTENSION}：`"
                    + os.path.abspath(process_path)
                    + "` -->\n\n"
                )
                total = len(data)
                if total > preview_n:
                    return_message += (
                        f"仅展示前 {preview_n} 条示例，共 {total} 条；完整结果见文件"
                    )
                else:
                    return_message += f"查询结果共计{total}条"
            else:
                sample_data = ""
                for i, file in enumerate(data):
                    sample_data += f"标题：{file['标题']}\n"
                    sample_data += f"文件时间：{file['文件时间']}\n"
                    if file.get("摘要", None):
                        sample_data += f"摘要：\"\"\"\n{file['摘要']}\n\"\"\"\n"
                    sample_data += f"file-type：{file['类型']}\n"
                    sample_data += f"file-id：{file['类型ID']}"
                    sample_data += "\n\n---\n\n"
                return_message += "### " + method_name_map[method_name] + "查询结果:\n\n---\n\n" + sample_data
                return_message += f"查询结果共计{len(data)}条"
    else:
        return_message = "调用gangtise服务端失败，错误信息：" + response["message"]
    if additional_message:
        return_message += "\n" + additional_message
    return return_message

def load_securities_from_file(path: str) -> List[str]:
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"证券文件不存在: {path}")
    df = pd.read_csv(full_path)
    if "security_abbr" in df.columns:
        return [str(x) for x in df["security_abbr"].dropna().tolist()]
    if "security_code" in df.columns:
        return [str(x) for x in df["security_code"].dropna().tolist()]
    raise ValueError("证券文件必须包含 security_code 或 security_abbr 列")

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
    print("检查 gangtise-kb 相关配置")
    if not get_authorization_token():
        print("  无法检测到gangtise密钥环境变量或授权文件, gangtise-kb 无法正常工作")
    else:
        print("  检测到gangtise授权文件, gangtise-kb 可以正常工作")
    if GTS_SAVE_FILE is None:
        print("  环境变量 GTS_SAVE_FILE 未配置, 默认值为 False, gangtise服务端 将不保存查询结果到文件中")
    elif GTS_SAVE_FILE == "True":
        print("  环境变量 GTS_SAVE_FILE 为 True, gangtise服务端 将保存查询结果到文件中")
    else:
        print("  环境变量 GTS_SAVE_FILE 为 False, gangtise服务端 将不保存查询结果到文件中")
    if check_version(large_version=False):
        print("  gangtise-kb 版本为最新")
    else:
        print("  gangtise-kb 版本不是最新, 建议进行更新")
    print(f"  gangtise-kb 工作文件目录: {WORK_PATH}")