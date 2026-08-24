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

from authorization import (
    authorized_request,
    get_authorization_headers,
    get_authorization_token,
    get_headers_extra,
    gangtise_domain,
    invalidate_authorization,
)

GTS_SAVE_FILE = os.getenv("GTS_SAVE_FILE", True)
# GTS_SAVE_EXTENSION = os.getenv("GTS_SAVE_EXTENSION", "md")
GTS_SAVE_EXTENSION = "md"

GANGTISE_OPENAI_DOMAIN = gangtise_domain("GANGTISE_OPENAI_DOMAIN", "https://openapi.gangtise.com/application/open-ai")
GANGTISE_REFERENCE_DOMAIN = gangtise_domain("GANGTISE_REFERENCE_DOMAIN", "https://openapi.gangtise.com/application/open-reference")
GANGTISE_AGENT_URL = GANGTISE_OPENAI_DOMAIN + "/agent"
HOT_TOPIC_LIST_URL = GANGTISE_OPENAI_DOMAIN + "/hot-topic/getList"
STOCK_SUMMARY_LIST_URL = GANGTISE_OPENAI_DOMAIN + "/stock-summary/getList"
SECURITIES_SEARCH_URL = GANGTISE_REFERENCE_DOMAIN + "/securities/search"
CONCEPT_SEARCH_URL = GANGTISE_REFERENCE_DOMAIN + "/concepts/search"
AGENTS_CN_MAP = {
    "stock-one-pager": "一页通",
    "investment-logic": "投资逻辑",
    "peer-comparison": "同业对比",
    "earnings-review": "业绩点评",
    "theme-tracking": "主题跟踪",
    "research-outline": "调研提纲",
    "viewpoint-debate": "观点PK",
    "stock-one-line-summary": "个股一句话总结",
    "hot-topic-list": "热点话题",
    "security-clue-list": "投研线索",
}

WORK_PATH = os.getenv("WORK_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workspace"))

if not os.path.exists(WORK_PATH):
    os.makedirs(WORK_PATH, exist_ok=True)

usage_dir = os.path.join(WORK_PATH, ".usage")
if not os.path.exists(usage_dir):
    os.makedirs(usage_dir, exist_ok=True)

file_dir = os.path.join(WORK_PATH, "files")
if not os.path.exists(file_dir):
    os.makedirs(file_dir, exist_ok=True)

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

_METHOD_NAME_CN_MAP = {
    "stock_one_pager": "一页通",
    "investment_logic": "投资逻辑",
    "peer_comparison": "同业对比",
    "earnings_review": "业绩点评",
    "theme_tracking": "主题跟踪",
    "research_outline": "调研提纲",
    "viewpoint_debate": "观点PK",
    "stock_one_line_summary": "个股一句话总结",
    "hot_topic_list": "热点话题",
    "security_clue_list": "投研线索",
    "agent": "Agent",
}

def _agent_display_name(agent_type: str, method_name: str) -> str:
    if agent_type and agent_type in AGENTS_CN_MAP:
        return AGENTS_CN_MAP[agent_type]
    return _METHOD_NAME_CN_MAP.get(method_name, method_name)

def _agent_item_args(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    abbr = str(row.get("securityAbbr") or "").strip()
    code = str(row.get("securityCode") or "").strip()
    if abbr and code:
        parts.append(f"{abbr} ({code})")
    elif abbr:
        parts.append(abbr)
    elif code:
        parts.append(code)
    for key in ("period", "type", "dataId", "viewpoint", "themeId", "theme_id"):
        val = str(row.get(key) or "").strip()
        if val and val not in parts:
            parts.append(val)
    return " ".join(parts)

def _agent_item_heading(agent_type: str, method_name: str, row: Dict[str, Any]) -> str:
    name = _agent_display_name(agent_type, method_name)
    args = _agent_item_args(row)
    if args:
        return f"{name} （{args}）"
    return name

def _write_agent_row(f, row: Dict[str, Any], agent_type: str, method_name: str) -> None:
    f.write(f"### {_agent_item_heading(agent_type, method_name, row)}\n")
    if row.get("date"):
        f.write(f"日期：{row['date']}\n")
    if row.get("type"):
        f.write(f"类型：{row['type']}\n")
    if row.get("dataId"):
        f.write(f"dataId：{row['dataId']}\n")
    if row.get("content"):
        content = row["content"].strip()
        f.write(f"内容：\"\"\"\n{content}\n\"\"\"\n")
    elif row.get("markdown"):
        f.write(row["markdown"].strip() + "\n")

def format_response(response: dict, method_name: str, output_dir: Optional[str] = None, additional_message: str = ""):
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

    if response.get("state") != "success":
        return_message = "调用gangtise服务端失败，错误信息：" + response.get("message", "")
        if additional_message:
            return_message += "\n" + additional_message
        return return_message

    sections: List[str] = []
    for item in response.get("data", []):
        data_list = item.get("data", [])
        module_name = item.get("module", "agent")
        type_name = item.get("type", "result")

        if module_name == "security":
            data = pd.DataFrame(data_list)
            sample_data = data_to_md(data)
            return "### 证券查询结果:\n\n" + sample_data
        if GTS_SAVE_FILE:
            extension = GTS_SAVE_EXTENSION
            if output_dir:
                process_dir = output_dir
            else:
                process_dir = os.path.join(WORK_PATH, method_name)
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
                return "错误信息：文件存储系统繁忙，请稍后再试"

            if GTS_SAVE_EXTENSION == "json":
                with open(process_path, "w", encoding="utf-8") as f:
                    json.dump(data_list, f, ensure_ascii=False, indent=2)
            else:
                with open(process_path, "w", encoding="utf-8") as f:
                    for i, row in enumerate(data_list):
                        _write_agent_row(f, row, type_name, method_name)
                        if i < len(data_list) - 1:
                            f.write("\n---\n\n")

            # 落盘时正文最多展示 2 条示例，完整结果见文件
            preview_n = 2
            preview = data_list[:preview_n]
            section = _render_agent_items(
                preview, type_name=type_name, method_name=method_name
            )
            total = len(data_list)
            if total > preview_n:
                section += (
                    f"\n\n（仅展示前 {preview_n} 条示例，共 {total} 条；"
                    f"完整结果见文件）"
                )
            else:
                section += f"\n\n查询结果共计 {total} 条"
            section += (
                f"\n\n<!-- 所有查询结果已保存到{GTS_SAVE_EXTENSION}：`"
                f"{os.path.abspath(process_path)}` -->"
            )
            sections.append(section)
        else:
            sections.append(
                _render_agent_items(data_list, type_name=type_name, method_name=method_name)
            )

    return_message = response.get("message", "")
    if sections:
        return_message += "\n\n" + "\n\n".join(sections)
    if additional_message:
        return_message += "\n" + additional_message
    return return_message.strip()

def _render_agent_items(
    data_list: List[Dict[str, Any]],
    type_name: str = "",
    method_name: str = "",
) -> str:
    if not data_list:
        return "无可展示数据。"
    parts: List[str] = []
    for row in data_list:
        parts.append(f"### {_agent_item_heading(type_name, method_name, row)}")
        if type_name in ("hot-topic-list", "security-clue-list") and row.get("markdown"):
            parts.append(str(row["markdown"]).strip())
            parts.append("---")
            continue
        if row.get("date"):
            parts.append(f"日期：{row['date']}")
        if row.get("type"):
            parts.append(f"类型：{row['type']}")
        if row.get("dataId"):
            parts.append(f"dataId：{row['dataId']}")
        if row.get("content"):
            content = row["content"].strip()
            parts.append(f"内容：\"\"\"\n{content}\n\"\"\"")
        parts.append("---")
    return "\n".join(parts[:-1] if parts and parts[-1] == "---" else parts)

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

def remove_html_tags(text):
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

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
    print("检查 gangtise-agent 相关配置")
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
        print("  gangtise-agent 版本为最新")
    else:
        print("  gangtise-agent 版本不是最新, 建议进行更新")
    print(f"  gangtise-agent 工作文件目录: {WORK_PATH}")