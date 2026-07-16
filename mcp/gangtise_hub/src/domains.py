"""五个业务域入口定义（工具名与 Skill 对齐）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class DomainDef:
    """对外 MCP 工具名 → 域包目录名 / 概览文案。"""

    tool_name: str
    package_dir: str
    title: str
    summary: str
    when_to_use: str


# 顺序即 tools/list 顺序；tool_name 与 openapi Skill 名一致
DOMAINS: Tuple[DomainDef, ...] = (
    DomainDef(
        tool_name="gangtise-data",
        package_dir="gangtise_data",
        title="结构化金融数据",
        summary=(
            "行情、财务、估值、资金流向、行业/公司指标、题材指数、板块成分与证券解析等；"
            "结果多为可落盘 CSV / 题材 Markdown。"
        ),
        when_to_use="需要精确数值、时间序列或宽表，或题材成分/投资逻辑画像时。",
    ),
    DomainDef(
        tool_name="gangtise-file",
        package_dir="gangtise_file",
        title="文件中心检索",
        summary=(
            "研报、公告、纪要、观点、外资研报/观点、公众号、投资者问答、投研日历等；"
            "返回文件 ID 与元数据/摘要，可按需下载全文。"
        ),
        when_to_use="按类型/日期/证券等定位、筛选文档列表，再决定是否下载时。",
    ),
    DomainDef(
        tool_name="gangtise-agent",
        package_dir="gangtise_agent",
        title="Agent 投研文本",
        summary=(
            "一页纸、投资逻辑、同业对比、业绩点评、观点辩论、主题跟踪、调研提纲、"
            "个股一句话、热点话题与投研线索等 Markdown 可读结论文本。"
        ),
        when_to_use="需要快速可读、少跳转的投研摘要与观点组织时。",
    ),
    DomainDef(
        tool_name="gangtise-kb",
        package_dir="gangtise_kb",
        title="知识库语义检索",
        summary="内部知识库向量检索，返回相关文本片段。",
        when_to_use="需要扫看文档中相关表述（论点、结论、段落），且不强调先筛文件列表时。",
    ),
    DomainDef(
        tool_name="gangtise-private",
        package_dir="gangtise_private",
        title="个人私有数据",
        summary="自选股池、微信群消息、我的会议、录音速记、AI 云盘等（仅当前授权用户本人数据）。",
        when_to_use="需要读取终端个人侧数据并与公开 data/file 能力衔接时。",
    ),
)

DOMAIN_BY_TOOL: Dict[str, DomainDef] = {d.tool_name: d for d in DOMAINS}

ROUTER_ACTIONS = ("list", "read_ref", "call")

ROUTER_INPUT_SCHEMA: Dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ROUTER_ACTIONS),
            "default": "list",
            "description": (
                "list（默认）= 返回类似 SKILL.md 的下级能力目录与调用示例；"
                "read_ref = 读取某叶子工具的完整参数说明（对应 references/<name>.yaml）；"
                "call = 调用叶子工具并传入 arguments_json"
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "叶子工具名。action=read_ref 或 call 时必填，"
                "例如 quote、financial、report、kb、stockpool"
            ),
        },
        "arguments_json": {
            "type": "string",
            "description": (
                "传给叶子工具的参数 JSON 对象字符串（仅 action=call 时使用）。"
                "示例：{\"securities\":\"贵州茅台\",\"start_date\":\"2024-01-01\"}"
            ),
        },
    },
}


def domain_tool_description(domain: DomainDef) -> str:
    return (
        f"{domain.title}。{domain.summary} "
        f"适用：{domain.when_to_use} "
        "默认 action=list 获取下级工具目录；"
        "确认参数后用 action=read_ref 查看详情，再用 action=call 执行。"
    )


def all_domain_tool_names() -> List[str]:
    return [d.tool_name for d in DOMAINS]
