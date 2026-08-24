import os
import sys
import json
import datetime
from typing import Dict, List, Any, NoReturn, Optional
import requests

from authorization import (
    authorized_request,
    get_authorization_headers,
    get_authorization_token,
    get_headers_extra,
    gangtise_domain,
    invalidate_authorization,
)

GTS_SAVE_FILE = os.getenv("GTS_SAVE_FILE", True)
GTS_SAVE_EXTENSION = os.getenv("GTS_SAVE_EXTENSION", "json")

GANGTISE_INDICATOR_DOMAIN = gangtise_domain(
    "GANGTISE_INDICATOR_DOMAIN", "https://openapi.gangtise.com/application/open-indicator"
)
GANGTISE_REFERENCE_DOMAIN = gangtise_domain(
    "GANGTISE_REFERENCE_DOMAIN", "https://openapi.gangtise.com/application/open-reference"
)
GANGTISE_QUOTE_DOMAIN = gangtise_domain(
    "GANGTISE_QUOTE_DOMAIN", "https://openapi.gangtise.com/application/open-quote"
)
INDICATOR_URL = os.getenv("INDICATOR_URL", GANGTISE_INDICATOR_DOMAIN + "/EDE/search")
INDICATOR_STOCK_URL = os.getenv("INDICATOR_STOCK_URL", GANGTISE_INDICATOR_DOMAIN + "/screener")
SECTOR_SEARCH_URL = os.getenv("SECTOR_SEARCH_URL", GANGTISE_REFERENCE_DOMAIN + "/sectors/search")
SECURITIES_SEARCH_URL = os.getenv(
    "SECURITIES_SEARCH_URL", GANGTISE_REFERENCE_DOMAIN + "/securities/search"
)
QUOTE_URL = os.getenv("QUOTE_URL", GANGTISE_QUOTE_DOMAIN + "/kline/daily")
HTTP_TIMEOUT = 600

def die(message: str, code: int = 1) -> NoReturn:
    """Print error message and exit."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)

WORK_PATH = os.getenv("WORK_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workspace"))
if not os.path.exists(WORK_PATH):
    os.makedirs(WORK_PATH, exist_ok=True)

usage_dir = os.path.join(WORK_PATH, ".usage")
if not os.path.exists(usage_dir):
    os.makedirs(usage_dir, exist_ok=True)

file_dir = os.path.join(WORK_PATH, "files")
if not os.path.exists(file_dir):
    os.makedirs(file_dir, exist_ok=True)

def add_usages(usages_list: List[Dict[str, Any]]):
    usages = {}
    for usages_item in usages_list:
        if len(usages_item) == 0:
            continue
        for k, v in usages_item.items():
            if k not in usages:
                usages[k] = v
            else:
                usages[k] = usages[k] + v
    return usages

def save_data_csv(
    records: List[Dict[str, Any]],
    method_name: str = "screener",
    module_name: str = "screener",
    output_dir: Optional[str] = None,
) -> str:
    """将结果落盘为 CSV（对齐 gangtise-data format_response 的 data 路径），返回绝对路径。"""
    import csv
    import time

    if not records:
        raise ValueError("records 不能为空")

    if output_dir:
        process_dir = output_dir
    else:
        process_dir = os.path.join(WORK_PATH, method_name)
    os.makedirs(process_dir, exist_ok=True)

    now = datetime.datetime.now().strftime("%H%M%S")
    process_path = os.path.join(process_dir, f"{module_name}_{now}.csv")
    max_retries = 10
    while os.path.exists(process_path) and max_retries > 0:
        time.sleep(1)
        now = datetime.datetime.now().strftime("%H%M%S")
        process_path = os.path.join(process_dir, f"{module_name}_{now}.csv")
        max_retries -= 1
    if max_retries == 0:
        raise RuntimeError("文件存储系统繁忙，请稍后再试")

    # 稳定列序：先按首行键，后续行多出的键追加在末尾
    fieldnames: List[str] = list(records[0].keys())
    for rec in records[1:]:
        for k in rec.keys():
            if k not in fieldnames:
                fieldnames.append(k)

    with open(process_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in fieldnames})

    return os.path.abspath(process_path)

def format_response(
    resp_text: str,
    method_name: str,
    module_name: str,
    usage: dict = None,
    output_dir: Optional[str] = None,
) -> str:
    # 保存usage
    usage = usage or {}
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    now = datetime.datetime.now().strftime("%H%M%S")
    usage_path = os.path.join(usage_dir, f"{today}.json")
    if usage:
        if os.path.exists(usage_path):
            with open(usage_path, "r", encoding="utf-8") as f:
                _usage = json.load(f)
            if now in usage:
                now_usage = add_usages([usage, _usage[now]])
            else:
                now_usage = usage
            _usage.update({now: now_usage})
        else:
            _usage = {now: usage}
        with open(usage_path, "w", encoding="utf-8") as f:
            json.dump(_usage, f, ensure_ascii=False)

    # 保存结果
    if GTS_SAVE_FILE in [True, 'true', '1', 'True']:
        if output_dir:
            process_dir = output_dir
        else:
            process_dir = os.path.join(WORK_PATH, method_name)
        os.makedirs(process_dir, exist_ok=True)
        extension = 'json'
        process_path = os.path.join(process_dir, f"{module_name}_{today}_{now}.{extension}")
        with open(process_path, "w", encoding="utf-8") as f:
            f.write(resp_text)
        resp_text += f"\n\n工具调用结果已保存到文件：\n`{os.path.abspath(process_path)}`\n\n"

    return resp_text

OPENAPI_SKILL_VERSION = "1.6.7"
SKILL_CHECK_URL = "https://open.gangtise.com/application/skills-backend/version?skill=openapi"

def check_version(large_version: bool = True):
    response = requests.get(SKILL_CHECK_URL)
    if response.status_code == 200 and large_version:
        return response.json()["state"] == "success" and response.json()["version"].split(".")[0] == \
            OPENAPI_SKILL_VERSION.split(".")[0] and response.json()["version"].split(".")[1] == \
            OPENAPI_SKILL_VERSION.split(".")[1]
    elif response.status_code == 200 and not large_version:
        return response.json()["state"] == "success" and response.json()["version"] == OPENAPI_SKILL_VERSION
    else:
        return False

if __name__ == "__main__":
    print("检查 gangtise-file 相关配置")
    if not get_authorization_token():
        print("  无法检测到gangtise密钥环境变量或授权文件, gangtise-agent 无法正常工作")
    else:
        print("  检测到gangtise授权文件, gangtise-agent 可以正常工作")
    if GTS_SAVE_FILE is None:
        print("  环境变量 GTS_SAVE_FILE 未配置, 默认值为 False, gangtise服务端 将不保存查询结果到文件中")
    elif GTS_SAVE_FILE == "True":
        print("  环境变量 GTS_SAVE_FILE 为 True, gangtise服务端 将保存查询结果到文件中")
    else:
        print("  环境变量 GTS_SAVE_FILE 为 False, gangtise服务端 将不保存查询结果到文件中")
    if check_version(large_version=False):
        print("  gangtise-file 版本为最新")
    else:
        print("  gangtise-file 版本不是最新, 建议进行更新")
    print(f"  gangtise-file 工作文件目录: {WORK_PATH}")