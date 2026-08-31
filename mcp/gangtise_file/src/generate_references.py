#!/usr/bin/env python3
"""根据 tools_registry 与内置说明生成 src/references/*.yaml。"""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, get_args, get_origin, get_type_hints

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from skill_reference_loader import load_tool_reference, _sanitize_mcp_text  # noqa: E402
from gangtise_file.tools_registry import INTERNAL_PARAMS, TOOL_HANDLERS  # noqa: E402

REFERENCES_DIR = SRC / "gangtise_file" / "references"

# MCP 工具说明：面向调用方，说明用途、场景与关键限制（不含脚本路径、内部 API 名）
TOOL_DESCRIPTIONS: Dict[str, str] = {
    "stock_one_pager": (
        "生成单只或多只股票的「一页纸」投研摘要，整合基本面要点、近期催化与市场观点，"
        "适合快速了解标的全貌。支持证券名称（推荐）、简称、拼音或代码。"
    ),
    "investment_logic": (
        "梳理指定股票的投资逻辑，包括核心驱动、关键假设、风险点与验证路径，"
        "适用于撰写研报提纲或投委会材料前的逻辑整理。"
    ),
    "peer_comparison": (
        "对指定股票进行同业/可比公司对比，覆盖估值、盈利、成长、市场地位等维度，"
        "便于相对定价与赛道内排位判断。"
    ),
    "earnings_review": (
        "针对指定报告期（如 2025q3）生成业绩点评：收入利润拆解、超预期/低于预期点、"
        "管理层表述与后续展望。需同时提供证券与 period。"
    ),
    "viewpoint_debate": (
        "围绕用户给出的投资观点（不超过 1000 字）生成正反方辩论式分析，"
        "帮助检验观点完备性与潜在反驳论据。可选关联合适标的。"
    ),
    "theme_tracking": (
        "按主题 ID 或中文主题名追踪题材资讯（晨报/晚报等），返回指定日期的跟踪内容。"
        "主题名不唯一时会返回候选列表供确认。"
    ),
    "research_outline": (
        "为指定股票生成深度研究大纲，覆盖行业、公司、财务、估值、催化剂等章节结构，"
        "可作为撰写长篇研报的骨架。"
    ),
    "stock_one_line_summary": (
        "批量获取个股「一句话总结」；支持具体证券列表，或 aShares/hkStocks 全市场扫描。"
        "无总结的标的不会返回。"
    ),
    "hot_topic": (
        "获取热点话题报告列表（早报/午报/盘中快报/晚报），含驱动事件、投资逻辑、"
        "核心标的等结构化内容，可按日期区间与报告类型筛选。"
    ),
    "security_clue": (
        "按证券或行业检索「证券线索」：聚合研报、纪要、公告、观点等来源的相关片段，"
        "用于快速搜集与标的相关的多源信息。"
    ),
    "block_constituents": (
        "按板块关键词或直接指定板块 ID，获取行业/概念/指数等板块的成分股列表，输出 CSV。"
        "关键词搜索仅返回匹配项（非全量板块目录），会排除指数成份类板块。"
    ),
    "company_indicator": (
        "检索或拉取 A 股公司指标（EDE）：行情、三大财报科目、财务比率、分红、"
        "盈利预测、公司/证券属性（含 scr_concept 证券所属概念）等。"
        "仅 keyword 时免费检索；indicator_codes + securities 时取数（收费）。"
        "产品销量请用 industry_indicator；券商一致预期请用 earning_forecast。"
        "财报截面日期用季度末；日频行情不含当天实时。"
    ),
    "concept": (
        "查询题材指数画像：定义、投资逻辑、行业空间、竞争格局、催化事件，"
        "以及按分组展示的成分股（含重点个股与纳入理由）。输出 Markdown 便于阅读。"
        "query_type 支持 info（仅画像）/ securities（仅成分股）/ all（默认，两者兼有）。"
        "多题材批量查询时各题材以 --- 分隔；未配置字段会省略对应章节。"
    ),
    "earning_forecast": (
        "拉取券商一致预期盈利预测，支持多指标与日期区间筛选，输出 CSV，"
        "适用于盈利预测与共识对比分析。"
    ),
    "financial": (
        "拉取财务报表（利润表/资产负债表/现金流量表），支持报告期 Q1–Q4/Q0、"
        "累计/单季等粒度，输出 CSV。"
    ),
    "fund_flow": (
        "查询 A 股日资金流向（小/中/大/特大单及主力净流入），"
        "支持指定证券或全市场，输出 CSV。仅历史数据，不含实时。"
    ),
    "industry_indicator": (
        "检索宏观/行业/大宗商品/产品类指标元信息，或按指标 ID 拉取时序数据（收费）。"
        "仅 keyword 时免费检索；indicator_ids 时取数。单次最多 10 个 ID，超出自动分批。"
        "产品销量仅在本工具；宏观默认中国；大宗商品避免泛用「价格」。"
    ),
    "main_business": (
        "查询上市公司主营构成，按产品/行业/地区等维度拆分，支持报告期 Q2/Q4，输出 CSV。"
    ),
    "quote": (
        "查询行情：日 K、分钟 K 或截面快照；支持前/后复权、全市场 cn/hk/us、日期区间，输出 CSV。"
        "分钟线不支持全市场与复权；缺复权因子的证券会单独按不复权返回，不影响其余标的。"
    ),
    "security": (
        "证券代码解析与搜索：按名称、简称、拼音或代码查询证券列表，用于确认标的代码与市场。"
    ),
    "shareholder": (
        "查询股东结构：前十大股东或前十大流通股东，支持日期区间或财报年度筛选，输出 CSV。"
    ),
    "valuation": (
        "查询估值指标（PE/PB/PS/PEG/PCF 等）及历史分位，多指标并发，输出 CSV。"
    ),
    "announcement": (
        "检索 A 股/港股公告索引，可按证券、日期、公告类型筛选；可选下载全文。"
    ),
    "foreign_opinion": (
        "检索外资/海外机构观点索引，支持地区、评级、证券与机构等筛选；可选下载。"
    ),
    "foreign_report": (
        "检索外资/海外研报索引，支持页数、评级、行业、地区等筛选；可选下载。"
    ),
    "investment_calendar": (
        "检索投资日历：路演、调研、线下策略会、论坛、财报日历；"
        "财报日历区分已发布（可下载原文件）与尚未发布（日程预告）。"
    ),
    "management_discuss": (
        "获取上市公司管理层讨论与分析（MD&A），含半年报/年报正文或业绩会口径，"
        "需指定报告期、证券与讨论维度。"
    ),
    "qa": (
        "按证券拉取投资者问答（互动平台、电话会议、调研纪要），返回提问、回答、"
        "来源、问题类型及是否涉及重要信息；支持多维筛选。"
    ),
    "official_account": (
        "检索金融公众号文章，可按账号、证券、行业、日期筛选；可选下载正文。"
    ),
    "opinion": (
        "检索国内机构首席观点，支持券商、首席、题材、投研标签、来源类型等多维筛选；可选下载。"
    ),
    "report": (
        "检索国内卖方研报，支持机构、行业、评级、页数、语义标签等；可选下载 PDF/Markdown。"
    ),
    "report_image": (
        "按关键词搜索研报中的图片，返回图注、页码、页面内容描述及 chunkId；"
        "支持时间范围、研报 ID 过滤。"
    ),
    "summary": (
        "检索会议纪要/调研纪要，支持机构、行业、来源类型、参会角色等筛选；可选下载。"
    ),
    "pamirs_summary": (
        "检索帕米尔牵头机构下的专家纪要，支持关键词、证券、研究方向、类别、市场筛选；"
        "可选下载原始文件或 HTML。需已购买专家纪要数据库权限。"
    ),
    "get_announcement_types": (
        "返回公告分类树（A 股 cn 或港股 hk），用于 announcement 的 category_list 参数取值参考。"
    ),
    "get_chiefs": (
        "检索首席分析师列表，支持按姓名、机构、团队组合搜索，返回候选 ID 与简介。"
    ),
    "get_industries": (
        "返回行业分类与研究领域映射表，供观点、纪要、日历等检索工具筛选时使用。"
    ),
    "get_institutions": (
        "检索卖方/买方等机构列表，返回机构 ID，供研报、观点等检索工具筛选使用。"
    ),
    "get_regions": (
        "返回外资研报/观点支持的地区代码列表。"
    ),
    "get_file": (
        "按 file_id 与 file_type 下载检索结果中的单个文件。"
        "file_type 须与检索结果「类型」一致，枚举：研究报告、外资研报、公司公告、港股公告、美股公告、"
        "会议纪要、帕米尔专家纪要、外资独立观点、公众号、财报日历。"
    ),
    "kb": (
        "在内部知识库中按自然语言语义检索，直接返回高相关文档片段，适合先读内容再决定是否拉原文。"
        "若主要想按类型/日期/证券筛文件列表，请用研报/公告等检索类工具。"
    ),
    "private_record": (
        "检索个人录音与转写记录列表，或按 record_ids 获取原文/语音识别/AI 速记；需账号授权。"
    ),
    "private_meeting": (
        "检索个人电话会议列表，或按 conference_ids 获取会议语音识别或 AI 速记内容。"
    ),
    "private_cloud": (
        "检索个人云盘文件列表，或按 file_ids 获取文件正文。"
        "不含对话、AI 速记、AI 翻译文件夹。"
    ),
    "stockpool": (
        "检索自选股池列表，或按 pool_ids / all_pools 获取池内成分股。"
    ),
    "wechat_message": (
        "检索已绑定微信群列表，或按 wechat_group_id_list 拉取群聊消息。需已激活群消息助理。"
    ),
}

PARAM_DESCRIPTIONS: Dict[str, str] = {
    "security": "单个证券名称或代码（推荐名称，如 贵州茅台）",
    "securities": "证券名称或代码列表",
    "output_dir": "结果与下载文件保存目录（可选；默认由服务端工作区管理）",
    "keyword": "搜索关键词",
    "start_date": "起始日期，格式 yyyy-MM-dd",
    "end_date": "结束日期，格式 yyyy-MM-dd",
    "start_time": "起始时间，格式 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss",
    "end_time": "结束时间，格式 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss",
    "period": "报告期，如 2025q3（earnings_review）或 Q2/Q4（财务类）",
    "viewpoint": "投资观点文本，不超过 1000 字",
    "theme_id": "主题 ID 或中文主题名",
    "date": "日期 yyyy-MM-dd（theme_tracking）",
    "types": "资讯类型列表，如 morning、night",
    "page_from": "分页起始（从 0 开始）",
    "page_size": "每页条数",
    "category_list": "分类/类型列表；帕米尔专家纪要可选 companyAnalysis/industryAnalysis（或中文：公司分析/行业分析）",
    "market_list": "市场类别列表；可选 aShares/hkStocks/usChinaConcept/usStocks（或中文：A股/港股/美股中概/美股）",
    "industries": "行业或研究方向名称列表；可选值见 get_industries",
    "query_mode": "查询模式：bySecurity（按证券）或 byIndustry（按行业）",
    "source": "来源类型列表，如 researchReport、conference、announcement、view",
    "sector_id": "板块 ID（已知时可直接指定，跳过关键词搜索）",
    "top": "搜索返回条数上限",
    "concepts": "题材名称或 ID 列表",
    "query_type": "concept 查询类型：info / securities / all",
    "indicator_ids": "行业指标 ID 列表（检索或取数）",
    "indicator_codes": "公司指标代码列表（检索或取数）",
    "limit": "返回条数上限",
    "all_market": "是否查询全市场 A 股资金流向",
    "all_market_markets": "拉取指定市场全市场行情（与 securities 二选一）；可选值 cn（A股）/ hk（港股）/ us（美股），逗号分隔；",
    "data_type": "行情类型：daily（日K）/ minute / snap",
    "adjust_mode": "复权方式：none / forward / backward",
    "holder_type": "股东类型：top10 或 top10Float",
    "table_type": "财务报表类型：income / balance / cashflow",
    "granularity": "财务粒度：accumulated（累计）或 single（单季）",
    "fiscal_year": "财报年度列表，如 2024",
    "report_type": "财报合并类型筛选",
    "field_list": "返回字段列表",
    "breakdown": "主营拆分维度：product / industry / region",
    "consensus_list": "一致预期指标列表",
    "download": "是否下载文件全文",
    "download_types": "下载格式列表，如 pdf、markdown；帕米尔专家纪要为 original、html",
    "file_id": "文件 ID（来自检索类接口返回的 file_id / id 等字段）",
    "file_type": (
        "文件类型（须与检索结果「类型」一致）。枚举：研究报告、外资研报、公司公告、港股公告、美股公告、"
        "会议纪要、帕米尔专家纪要、外资独立观点、公众号、财报日历"
    ),
    "download_type": (
        "下载格式（按 file_type）：研究报告/公司公告/美股公告为 pdf、markdown；"
        "外资研报另支持 zh_pdf、zh_markdown；外资独立观点为 html、html_zh；"
        "帕米尔专家纪要为 original、html；公众号为 txt、html"
    ),
    "search_type": "搜索类型：1 标题搜索，2 全文搜索",
    "rank_type": "排序方式：1 综合排序，2 时间倒序",
    "query": "自然语言检索问句（kb）",
    "file_types": "kb 检索限定的文件类型列表",
    "kind": "投资日历活动类型",
    "discuss_type": "管理层讨论类型",
    "report_date": "报告期日期",
    "discussion_dimension": "讨论维度",
    "question_category_list": "问题类型列表，支持中文或英文 code",
    "answer_important": "答案是否涉及重要信息：1/是、0/否；all 或 0,1 表示不过滤",
    "market": "公告分类市场：cn（A股）或 hk（港股）",
    "name": "首席姓名",
    "institution": "机构名称关键词",
    "group": "团队/小组名称",
    "record_ids": "私有录音记录 ID 列表",
    "content_types": "内容格式列表，如 transcript、summary",
    "conference_ids": "会议 ID 列表",
    "file_ids": "云盘文件 ID 列表",
    "pool_ids": "股池 ID 列表",
    "all_pools": "是否获取全部股池成分",
    "room_name": "微信群名称关键词",
    "room_filter": "群过滤条件",
    "wechat_group_id_list": "微信群 ID 列表",
    "industry_id_list": "行业 ID 列表",
    "tag_list": "标签列表",
    "max_total": "消息最大返回条数",
    "params": "指标附加参数（高级用法）",
    "with_related_securities": "热点报告是否包含核心标的",
    "with_close_reading": "热点报告是否包含话题精读",
}

SKIP_PARAMS = INTERNAL_PARAMS | frozenset({"output_dir"})


def _sanitize_param_description(desc: str) -> str:
    s = (desc or "").strip()
    s = s.replace("【是】", "").replace("【条件必填】", "").strip()
    s = _sanitize_mcp_text(s)
    s = re.sub(
        r"可通过\s*查看可选值",
        "请先调用 get_announcement_types 获取可选值",
        s,
    )
    s = re.sub(r"\s+", " ", s).strip(" ，。;；")
    return s or desc


def _sanitize_supplements(text: str) -> str:
    """清理补充说明，保留 bullet 结构。"""
    if not text:
        return ""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            body = _sanitize_mcp_text(_clean_supplement_line(stripped[2:]))
            if body:
                lines.append(f"- {body}")
        else:
            body = _sanitize_mcp_text(_clean_supplement_line(stripped))
            if body:
                lines.append(body)
    return "\n".join(lines)


def _clean_supplement_line(text: str) -> str:
    s = text.replace("**", "")
    s = re.sub(r"^约束[：:]\s*", "", s)
    s = re.sub(r"^--type=", "query_type=", s)
    return s.strip()


def _compose_description(tool_name: str, skill_ref) -> str:
    base = TOOL_DESCRIPTIONS.get(
        tool_name,
        f"{tool_name.replace('_', ' ').title()} 工具。",
    ).strip()
    supplements = _sanitize_supplements(skill_ref.supplements or "")
    if not supplements:
        return base
    return f"{base}\n\n{supplements}"


def _unwrap_optional(tp: Any) -> Any:
    origin = get_origin(tp)
    if origin is None:
        return tp
    args = get_args(tp)
    if origin is list:
        return list
    if origin is dict:
        return dict
    non_none = [a for a in args if a is not type(None)]
    return non_none[0] if len(non_none) == 1 else tp


def _type_to_yaml(tp: Any) -> str:
    tp = _unwrap_optional(tp)
    if tp is str:
        return "string"
    if tp is int:
        return "integer"
    if tp is float:
        return "number"
    if tp is bool:
        return "boolean"
    origin = get_origin(tp)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return "string"


def _build_parameters(fn: Any, tool_name: str) -> Dict[str, Any]:
    skill_ref = load_tool_reference(tool_name)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    sig = inspect.signature(fn)
    params: Dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if name in SKIP_PARAMS or param.kind == param.VAR_KEYWORD:
            continue
        tp = hints.get(name, param.annotation)
        if tp is inspect.Parameter.empty:
            tp = str
        desc = (
            skill_ref.param_descriptions.get(name)
            or PARAM_DESCRIPTIONS.get(name)
            or name
        )
        desc = _sanitize_param_description(desc)
        pdef: Dict[str, Any] = {
            "type": _type_to_yaml(tp),
            "description": desc,
        }
        if _type_to_yaml(tp) == "array":
            pdef["items"] = {"type": "string"}
        if param.default is inspect.Parameter.empty:
            pdef["required"] = True
        # get_file：显式枚举，便于 MCP 客户端展示可选值
        if tool_name == "get_file" and name == "file_type":
            pdef["enum"] = [
                "研究报告",
                "外资研报",
                "公司公告",
                "港股公告",
                "美股公告",
                "会议纪要",
                "帕米尔专家纪要",
                "外资独立观点",
                "公众号",
                "财报日历",
            ]
        if tool_name == "get_file" and name == "download_type":
            pdef["enum"] = [
                "pdf",
                "markdown",
                "original",
                "html",
                "txt",
                "zh_pdf",
                "zh_markdown",
                "html_zh",
            ]
        params[name] = pdef
    return params


def _yaml_quote(s: str) -> str:
    if "\n" in s or ":" in s or s.startswith(('"', "'")):
        return json.dumps(s, ensure_ascii=False)
    return s


def _dump_yaml(data: Dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"name: {data['name']}")
    lines.append(f"title: {_yaml_quote(str(data.get('title', '')))}")
    desc = str(data.get("description", ""))
    if "\n" in desc:
        lines.append("description: |")
        for line in desc.splitlines():
            lines.append(f"  {line}")
    else:
        lines.append(f"description: {_yaml_quote(desc)}")
    lines.append("parameters:")
    for pname, pdef in (data.get("parameters") or {}).items():
        lines.append(f"  {pname}:")
        for k, v in pdef.items():
            if k == "items" and isinstance(v, dict):
                lines.append("    items:")
                for ik, iv in v.items():
                    lines.append(f"      {ik}: {iv}")
            elif k == "enum" and isinstance(v, list):
                lines.append("    enum:")
                for item in v:
                    lines.append(f"      - {_yaml_quote(str(item))}")
            else:
                if isinstance(v, bool):
                    val = "true" if v else "false"
                elif isinstance(v, str):
                    val = _yaml_quote(v)
                else:
                    val = v
                lines.append(f"    {k}: {val}")
    return "\n".join(lines) + "\n"


def generate() -> None:
    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    for name, handler in sorted(TOOL_HANDLERS.items()):
        path = REFERENCES_DIR / f"{name}.yaml"
        skill_ref = load_tool_reference(name)
        description = _compose_description(name, skill_ref)
        doc = {
            "name": name,
            "title": name.replace("_", " ").title(),
            "description": description,
            "parameters": _build_parameters(handler, name),
        }
        path.write_text(_dump_yaml(doc), encoding="utf-8")
        print(f"wrote {path.name}")


if __name__ == "__main__":
    generate()
