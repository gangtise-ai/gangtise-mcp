"""从 skills/openapi 参考文档解析工具说明与参数描述。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# mcps/mcp/gangtise_data/src -> skills-backend/skills/openapi
_SKILLS_CANDIDATES = [
    Path(__file__).resolve().parents[4] / "skills" / "openapi",
    Path(__file__).resolve().parents[3] / "skills" / "openapi",
    Path(__file__).resolve().parents[2] / "skills" / "openapi",
]


@dataclass
class SkillReference:
    """param_descriptions 来自 skill 文档；supplements 为可追加到主描述的使用提示。"""
    supplements: str = ""
    param_descriptions: Dict[str, str] = field(default_factory=dict)
    description: str = ""  # 已弃用，保留兼容


def _skills_root() -> Optional[Path]:
    for p in _SKILLS_CANDIDATES:
        if p.is_dir():
            return p
    return None


def _kebab_to_snake(name: str) -> str:
    return name.replace("-", "_")


# CLI 长选项 / 短选项 -> MCP Python 参数名
_FLAG_TO_PARAM: Dict[str, str] = {
    "start-date": "start_date",
    "end-date": "end_date",
    "start-time": "start_time",
    "end-time": "end_time",
    "keyword": "keyword",
    "query": "query",
    "securities": "securities",
    "security": "security",
    "limit": "limit",
    "period": "period",
    "viewpoint": "viewpoint",
    "theme-id": "theme_id",
    "date": "date",
    "page-from": "page_from",
    "page-size": "page_size",
    "hot-category": "category_list",
    "with-securities": "with_related_securities",
    "with-close-reading": "with_close_reading",
    "query-mode": "query_mode",
    "source": "source",
    "category-list": "category_list",
    "industries": "industries",
    "institution-list": "institution_list",
    "institutions": "institutions",
    "region-list": "region_list",
    "llm-tag-list": "llm_tag_list",
    "rating-list": "rating_list",
    "rating-change_list": "rating_change_list",
    "rating-change-list": "rating_change_list",
    "min-report_pages": "min_report_pages",
    "max-report_pages": "max_report_pages",
    "min-report-pages": "min_report_pages",
    "max-report-pages": "max_report_pages",
    "search-type": "search_type",
    "rank-type": "rank_type",
    "download": "download",
    "download-types": "download_types",
    "output-dir": "output_dir",
    "source-id": "source_id",
    "file-id": "file_id",
    "file-type": "file_type",
    "file-types": "file_types",
    "sector-id": "sector_id",
    "top": "top",
    "concepts": "concepts",
    "query-type": "query_type",
    "space-type-list": "space_type_list",
    "content-type": "content_types",
    "research-areas": "research_area_list",
    "research-area-list": "research_area_list",
    "security-list": "security_list",
    "file-type-list": "file_type_list",
    "room-name": "room_name",
    "groups": "wechat_group_id_list",
    "categories": "category_list",
    "tags": "tag_list",
    "industry-id-list": "industry_id_list",
    "tag-list": "tag_list",
    "max-total": "max_total",
    "room-filter": "room_filter",
    "name": "name",
    "institution": "institution",
    "group": "group",
    "market": "market",
    "params": "params",
    "holder-type": "holder_type",
    "fiscal-year": "fiscal_year",
    "report-type": "report_type",
    "field-list": "field_list",
    "breakdown": "breakdown",
    "consensus-list": "consensus_list",
    "adjust": "adjust_mode",
    "data-type": "data_type",
    "discuss-type": "discuss_type",
    "report-date": "report_date",
    "discussion-dimension": "discussion_dimension",
    "kind": "kind",
    "chiefs": "chiefs",
    "llm-tags": "llm_tags",
    "broker-type-list": "broker_type_list",
    "permission-list": "permission_list",
    "object-list": "object_list",
    "accounts": "accounts",
    "columns": "columns",
    "granularity": "granularity",
    "table-type": "table_type",
    "category": "category",
    "all-market": "all_market",
    "all": "all_pools",
    "pool": "pool_ids",
    "conference": "conference_ids",
    "files": "file_ids",
    "record": "record_ids",
    "type": "query_type",  # concept.py --type
}

_SHORT_TO_PARAM: Dict[str, str] = {
    "sd": "start_date",
    "ed": "end_date",
    "st": "start_time",
    "et": "end_time",
    "k": "keyword",
    "l": "limit",
    "p": "params",
    "t": "theme_id",
    "c": "concepts",
    "n": "room_name",
}

_TOOL_FLAG_OVERRIDES: Dict[str, Dict[str, str]] = {
    "company_indicator": {"indicators": "indicator_codes"},
    "industry_indicator": {"indicators": "indicator_ids"},
    "concept": {"type": "query_type"},
    "quote": {"all-market": "all_market_markets"},
}

# 已合并 search/get 的工具不再按模式过滤参数
_MODE_PARAM_HINTS: Dict[str, Set[str]] = {}

_AGENT_TOOL_PARAMS: Dict[str, Set[str]] = {
    "stock_one_pager": {"security", "securities"},
    "investment_logic": {"security", "securities"},
    "peer_comparison": {"security", "securities"},
    "earnings_review": {"security", "securities", "period"},
    "viewpoint_debate": {"viewpoint", "security", "securities"},
    "theme_tracking": {"theme_id", "date", "types"},
    "research_outline": {"security", "securities"},
    "stock_one_line_summary": {"security", "securities"},
    "hot_topic": {
        "page_from",
        "page_size",
        "start_date",
        "end_date",
        "category_list",
        "with_related_securities",
        "with_close_reading",
    },
    "security_clue": {
        "page_from",
        "page_size",
        "start_time",
        "end_time",
        "query_mode",
        "securities",
        "source",
    },
}

# tool_name -> (skill_dir, md_file, mode?, skill_section?)
_TOOL_SOURCES: Dict[str, Tuple[str, str, Optional[str], Optional[str]]] = {
    "block_constituents": ("gangtise-data", "block_constituents.md", None, None),
    "company_indicator": ("gangtise-data", "company_indicatory.md", None, None),
    "concept": ("gangtise-data", "concept.md", None, None),
    "earning_forecast": ("gangtise-data", "earning_forecast.md", None, None),
    "financial": ("gangtise-data", "financial.md", None, None),
    "fund_flow": ("gangtise-data", "fund_flow.md", None, None),
    "industry_indicator": ("gangtise-data", "industry_indicator.md", None, None),
    "main_business": ("gangtise-data", "main_business.md", None, None),
    "quote": ("gangtise-data", "quote.md", None, None),
    "shareholder": ("gangtise-data", "shareholder.md", None, None),
    "valuation": ("gangtise-data", "valuation.md", None, None),
    "announcement": ("gangtise-file", "announcement.md", None, None),
    "foreign_opinion": ("gangtise-file", "foreign_opinion.md", None, None),
    "foreign_report": ("gangtise-file", "foreign_report.md", None, None),
    "investment_calendar": ("gangtise-file", "investment_calendar.md", None, None),
    "management_discuss": ("gangtise-file", "management-discuss.md", None, None),
    "qa": ("gangtise-file", "qa.md", None, None),
    "official_account": ("gangtise-file", "official_account.md", None, None),
    "opinion": ("gangtise-file", "opinion.md", None, None),
    "report": ("gangtise-file", "report.md", None, None),
    "report_image": ("gangtise-file", "report_image.md", None, None),
    "summary": ("gangtise-file", "summary.md", None, None),
    "get_file": ("gangtise-file", "get_file.md", None, None),
    "get_announcement_types": ("gangtise-file", "announcement.md", None, "announcement_types"),
    "kb": ("gangtise-kb", "SKILL.md", None, "kb"),
    "private_record": ("gangtise-private", "private_record.md", None, None),
    "private_meeting": ("gangtise-private", "private_meeting.md", None, None),
    "private_cloud": ("gangtise-private", "private_cloud.md", None, None),
    "stockpool": ("gangtise-private", "stockpool.md", None, None),
    "wechat_message": ("gangtise-private", "wechat_message.md", None, None),
}

_AGENT_TOOLS = set(_AGENT_TOOL_PARAMS.keys()) - {"hot_topic", "security_clue"}

_STATIC_DOCS: Dict[str, SkillReference] = {
    "security": SkillReference(
        param_descriptions={
            "keyword": "搜索关键词（名称、代码、拼音等），必填。",
            "category": "证券品类过滤，如 stock、dr、index；可多选。",
            "top": "检索返回条数上限。",
            "limit": "最终输出条数上限。",
        },
    ),
    "get_chiefs": SkillReference(
        param_descriptions={
            "keyword": "综合检索词；可与 name/institution/group 组合。",
            "name": "首席姓名（模糊匹配，结果二次过滤）。",
            "institution": "所属机构名称关键词。",
            "group": "团队/小组名称。",
            "top": "返回条数上限，最大 10。",
        },
    ),
    "get_institutions": SkillReference(
        param_descriptions={
            "keyword": "机构名称或简称搜索关键词，必填。",
            "category_list": (
                "机构分类：domesticBroker、foreignInstitution、"
                "foreignOpinionInstitution、leadInstitution、opinionInstitution。"
            ),
            "top": "返回条数上限，最大 10。",
        },
    ),
    "get_industries": SkillReference(param_descriptions={}),
    "get_regions": SkillReference(param_descriptions={}),
    "qa": SkillReference(
        param_descriptions={
            "securities": "证券名称或代码列表，必填。",
            "start_date": "开始时间，yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss。",
            "end_date": "结束时间，格式同上。",
            "limit": "返回条数上限，默认 100，单页最大 500（自动分页）。",
            "source_list": (
                "问题来源，多选：conference（电话会议）、interactive（互动平台）、"
                "survey（调研纪要）；支持中文。"
            ),
            "question_category_list": (
                "问题类型，多选；如 productAndBusiness、financialData 或中文「产品技术与业务布局」。"
            ),
            "answer_important": "是否涉及重要信息：1/是 仅重要；0/否 仅非重要；all 不过滤。",
        },
    ),
}


def _clean_md_text(text: str, tool_name: Optional[str] = None) -> str:
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _sanitize_cli_flags(s, tool_name)


def _sanitize_cli_flags(text: str, tool_name: Optional[str] = None) -> str:
    if not text:
        return ""
    overrides = _TOOL_FLAG_OVERRIDES.get(tool_name, {}) if tool_name else {}
    s = text
    for flag, param in sorted(_FLAG_TO_PARAM.items(), key=lambda x: -len(x[0])):
        target = overrides.get(flag, param)
        s = re.sub(rf"--{re.escape(flag)}\b", target, s)
    for short, param in _SHORT_TO_PARAM.items():
        s = re.sub(rf"(?<![\w-])-{short}\b", param, s)
    s = re.sub(r"--([\w-]+)", lambda m: _kebab_to_snake(m.group(1)), s)
    return s


def _sanitize_mcp_text(text: str) -> str:
    """去掉脚本路径、Open API 等技术用语，保留对调用方有用的说明。"""
    if not text:
        return ""
    s = _sanitize_cli_flags(text)
    s = re.sub(r"scripts/[\w_.-]+\.py", "", s)
    s = re.sub(r"Gangtise\s+Open\s+API", "", s, flags=re.I)
    s = re.sub(r"open[-\s]?(reference|insight|vault|platform)[^\s，。；]*", "", s, flags=re.I)
    s = re.sub(r"(通过|基于)\s*接口", "", s)
    s = re.sub(r"主脚本[：:][^\s。]+", "", s)
    s = re.sub(r"本工具对应脚本的\s*(search|get)\s*模式[：:][^。\n]*", "", s)
    s = re.sub(r"\b(getList|download/file)\b", "", s)
    s = re.sub(r"\|\s*[-:]+\s*\|", " ", s)
    s = re.sub(r"\|", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ，。;；")
    return s


def _extract_constraint_bullets(md: str) -> str:
    """从「约束与说明」提取 - 开头的要点。"""
    lines = md.splitlines()
    capturing = False
    level = 0
    bullets: List[str] = []
    for line in lines:
        m = re.match(r"^(#{2,})\s+(.+)$", line)
        if m:
            h_level = len(m.group(1))
            title = m.group(2).strip()
            if title in ("约束与说明", "约束"):
                capturing = True
                level = h_level
                continue
            if capturing and h_level <= level:
                break
        if not capturing:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            text = _sanitize_mcp_text(_clean_md_text(stripped[2:]))
            if text and len(text) > 8:
                bullets.append(f"- {text}")
    return "\n".join(bullets)


def _extract_section(md: str, headings: Tuple[str, ...], max_chars: int = 0) -> str:
    pattern = r"^##+\s+(" + "|".join(re.escape(h) for h in headings) + r")\s*$"
    lines = md.splitlines()
    out: List[str] = []
    capturing = False
    level = 0
    for line in lines:
        m = re.match(r"^(#{2,})\s+(.+)$", line)
        if m:
            h_level = len(m.group(1))
            title = m.group(2).strip()
            if re.match(pattern, line, re.IGNORECASE):
                capturing = True
                level = h_level
                continue
            if capturing and h_level <= level:
                break
        if capturing:
            if line.strip().startswith("```"):
                continue
            if line.strip().startswith("|") and "参数" in line:
                break
            stripped = line.strip()
            if stripped:
                out.append(_clean_md_text(stripped))
    text = " ".join(out).strip()
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _extract_constraints(md: str) -> str:
    raw = _extract_section(md, ("约束与说明", "约束"))
    return raw


def _extract_table_bullets(md: str, heading: str) -> str:
    """将 ## heading 下的 Markdown 表格转为 bullet 列表。"""
    lines = md.splitlines()
    capturing = False
    level = 0
    bullets: List[str] = []
    for line in lines:
        m = re.match(r"^(#{2,})\s+(.+)$", line)
        if m:
            h_level = len(m.group(1))
            title = m.group(2).strip()
            if title == heading or heading in title:
                capturing = True
                level = h_level
                continue
            if capturing and h_level <= level:
                break
        if not capturing or not line.strip().startswith("|"):
            continue
        if re.match(r"^\|[-:\s|]+\|$", line.strip()):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = _clean_md_text(cells[0])
        if label in ("参数", "模式", "类型", "场景", "说明"):
            continue
        bullets.append(f"- **{label}**：{_clean_md_text(cells[1])}")
    return "\n".join(bullets)


def _parse_table_rows(md: str, section_markers: Optional[Tuple[str, ...]] = None) -> List[Tuple[str, str, str]]:
    """返回 [(flag_cell, required_cell, description), ...]"""
    lines = md.splitlines()
    rows: List[Tuple[str, str, str]] = []
    in_target = section_markers is None
    for i, line in enumerate(lines):
        if section_markers and re.match(r"^##+\s+", line):
            title = re.sub(r"^#+\s*", "", line).strip()
            in_target = any(m in title for m in section_markers)
        if not in_target:
            continue
        if not line.strip().startswith("|"):
            continue
        if "参数" in line and ("说明" in line or "必填" in line):
            continue
        if re.match(r"^\|[-:\s|]+\|$", line.strip()):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0] in ("参数", "模式"):
            continue
        if len(cells) >= 3 and ("必填" in cells[1] or cells[1] in ("是", "否", "条件必填")):
            rows.append((cells[0], cells[1], cells[2]))
        else:
            rows.append((cells[0], "", cells[1] if len(cells) == 2 else cells[-1]))
    return rows


def _flags_to_param(tool_name: str, flag_cell: str) -> Optional[str]:
    overrides = _TOOL_FLAG_OVERRIDES.get(tool_name, {})
    long_flags = re.findall(r"--([\w-]+)", flag_cell)
    for lf in long_flags:
        if lf in overrides:
            return overrides[lf]
        if lf in _FLAG_TO_PARAM:
            return _FLAG_TO_PARAM[lf]
        return _kebab_to_snake(lf)
    short_flags = re.findall(r"(?<![\w-])-([a-z])\b", flag_cell)
    for sf in short_flags:
        if sf in _SHORT_TO_PARAM:
            return _SHORT_TO_PARAM[sf]
    # 无 flag 的纯参数名（如 get_announcement_types 的 market）
    plain = flag_cell.strip()
    if plain and re.match(r"^[a-z][\w_]*$", plain):
        return plain
    return None


def _trim_mode_description(mode: Optional[str], desc: str) -> str:
    if not mode or mode not in ("search", "get"):
        return desc
    if mode == "search" and "search：" in desc:
        part = desc.split("get：", 1)[0].strip()
        return part.removeprefix("search：").strip() if part.startswith("search：") else part
    if mode == "get" and "get：" in desc:
        idx = desc.find("get：")
        return desc[idx + 4 :].strip()
    return desc


def _mode_applies(tool_name: str, param_name: str, desc: str, required: str) -> bool:
    hints = _MODE_PARAM_HINTS.get(tool_name)
    if not hints:
        return True
    if param_name in hints:
        return True
    blob = f"{desc} {required}".lower()
    if "search" in blob and "get" in blob:
        return True
    mode = tool_name.rsplit("_", 1)[-1] if "_" in tool_name else ""
    if mode == "search" and ("(get" in blob or "get 模式" in blob or "get*" in required):
        return False
    if mode == "get" and ("(search" in blob or "search 模式" in blob or "search*" in required):
        return False
    return param_name not in hints


def _build_param_map(
    tool_name: str,
    rows: List[Tuple[str, str, str]],
    mode: Optional[str] = None,
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for flag_cell, required, desc in rows:
        long_flags = re.findall(r"--([\w-]+)", flag_cell)
        param_names: List[str] = []
        if len(long_flags) > 1:
            for lf in long_flags:
                pname = _TOOL_FLAG_OVERRIDES.get(tool_name, {}).get(lf)
                if not pname:
                    pname = _FLAG_TO_PARAM.get(lf) or _kebab_to_snake(lf)
                param_names.append(pname)
        else:
            pname = _flags_to_param(tool_name, flag_cell)
            if pname:
                param_names.append(pname)
        if not param_names:
            continue
        for pname in param_names:
            if not _mode_applies(tool_name, pname, desc, required):
                continue
            text = _trim_mode_description(mode, _clean_md_text(desc, tool_name))
            if required.strip() in ("是", "条件必填"):
                text = f"【{required.strip()}】{text}"
            if pname not in out or len(text) > len(out[pname]):
                out[pname] = text
    return out


def _parse_agent_skill(md: str, tool_name: str) -> SkillReference:
    ref = SkillReference()
    if tool_name in ("hot_topic", "security_clue"):
        section = f"scripts/{tool_name}.py"
    else:
        section = "scripts/agents.py"

    # 截取 ### `scripts/...` 下的表格
    section_pat = re.compile(
        rf"###\s*`{re.escape(section)}`\s*\n(.*?)(?=\n###\s|\Z)",
        re.DOTALL,
    )
    sm = section_pat.search(md)
    table_md = sm.group(1) if sm else md
    rows = _parse_table_rows(table_md)
    param_map = _build_param_map(tool_name, rows)

    allowed = _AGENT_TOOL_PARAMS.get(tool_name)
    if allowed:
        param_map = {k: v for k, v in param_map.items() if k in allowed}
        param_map = _simplify_agent_param_desc(tool_name, param_map)

    ref.param_descriptions = param_map
    return ref


def _simplify_agent_param_desc(tool_name: str, param_map: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pname, desc in param_map.items():
        text = desc.replace("【条件必填】", "").strip()
        if pname == "security":
            out[pname] = "单个证券名称或代码（推荐名称，如 贵州茅台；或代码 600519.SH）。"
        elif pname == "securities":
            base = "多个证券名称或代码，逗号分隔；与 security 可同时使用并去重合并。"
            if tool_name == "stock_one_line_summary":
                base += "另支持 aShares（全部 A 股）或 hkStocks（全部港股），不可与具体证券混传。"
            out[pname] = base
        elif pname == "period":
            out[pname] = "报告期，如 2025q3。"
        elif pname == "viewpoint":
            out[pname] = "投资观点文本，不超过 1000 字。"
        elif pname == "theme_id":
            out[pname] = "主题 ID 或中文主题名；名称不唯一时返回候选列表。"
        elif pname == "date":
            out[pname] = "查询日期，格式 yyyy-MM-dd。"
        elif pname == "types":
            out[pname] = "资讯类型：morning（晨报）、night（晚报），可逗号多选。"
        elif pname == "category_list":
            out[pname] = "报告类型：morning（早报）、noon（午报）、afternoon（盘中快报）、evening（晚报），逗号分隔。"
        elif pname == "query_mode":
            out[pname] = "查询方式：bySecurity（按证券）或 byIndustry（按行业）。"
        elif pname == "source":
            out[pname] = "来源多选：researchReport（研报）、conference（纪要）、announcement（公告）、view（观点）。"
        else:
            out[pname] = text
    return out


def _parse_kb_skill(md: str) -> SkillReference:
    ref = SkillReference()
    rows = _parse_table_rows(md, section_markers=("知识库调用指南", "参数"))
    ref.param_descriptions = _build_param_map("kb", rows)
    # kb 的 -q 是 query
    if "query" not in ref.param_descriptions:
        for flag_cell, required, desc in rows:
            if "-q" in flag_cell or "--query" in flag_cell:
                ref.param_descriptions["query"] = _clean_md_text(desc)
    return ref


def _parse_announcement_types(md: str) -> SkillReference:
    ref = SkillReference()
    # 「获取公告分类列表」小节
    m = re.search(r"##\s+获取公告分类列表\s*\n(.*?)(?=\n##\s|\Z)", md, re.DOTALL)
    if m:
        rows = _parse_table_rows(m.group(1))
        ref.param_descriptions = _build_param_map("get_announcement_types", rows)
    if "market" not in ref.param_descriptions:
        ref.param_descriptions["market"] = "市场，可选 cn（A股）或 hk（港股），默认 cn。"
    return ref


def _build_supplements(md: str, extra: str = "") -> str:
    """仅附加来自文档表格的要点（约束章节多含 CLI/API 用语，不写入 MCP 描述）。"""
    return (extra or "").strip()


def load_tool_reference(tool_name: str) -> SkillReference:
    if tool_name in _STATIC_DOCS:
        return _STATIC_DOCS[tool_name]

    if tool_name in _AGENT_TOOL_PARAMS:
        root = _skills_root()
        if not root:
            return SkillReference()
        skill_md = (root / "gangtise-agent" / "SKILL.md").read_text(encoding="utf-8")
        return _parse_agent_skill(skill_md, tool_name)

    cfg = _TOOL_SOURCES.get(tool_name)
    if not cfg:
        return SkillReference()

    root = _skills_root()
    if not root:
        return SkillReference()

    skill_dir, md_file, mode, section = cfg
    if md_file == "SKILL.md":
        path = root / skill_dir / md_file
    else:
        path = root / skill_dir / "references" / md_file
    if not path.is_file():
        return SkillReference()

    md = path.read_text(encoding="utf-8")

    if section == "kb":
        return _parse_kb_skill(md)
    if section == "announcement_types":
        return _parse_announcement_types(md)

    intro = _extract_section(md, ("简介", "概览"))
    _ = intro  # 简介含脚本/API 用语，不写入 MCP 描述
    extra = ""
    if tool_name == "industry_indicator":
        extra = _extract_table_bullets(md, "覆盖范围与检索技巧")
    elif tool_name == "company_indicator":
        extra = _extract_table_bullets(md, "覆盖范围与检索技巧")
    ref = SkillReference(supplements=_build_supplements(md, extra))

    rows = _parse_table_rows(md)
    ref.param_descriptions = _build_param_map(tool_name, rows, mode)
    ref.param_descriptions = _apply_indicator_param_hints(tool_name, ref.param_descriptions)
    ref.param_descriptions.pop("output_dir", None)
    return ref


def _apply_indicator_param_hints(
    tool_name: str,
    params: Dict[str, str],
) -> Dict[str, str]:
    out = dict(params)
    if tool_name == "company_indicator":
        out["keyword"] = (
            "检索关键词，须为具体指标词（如「收盘价」「成交量」「营业收入」「所属概念」），"
            "避免过于笼统的表述。"
        )
        out["indicator_codes"] = (
            "指标编码或名称，逗号分隔；提供后与 securities 一并传入则拉取数据。"
        )
        out["start_date"] = (
            "开始日期 yyyy-MM-dd。财报类截面指标通常设为报告期季度末"
            "（如 2026-03-31）；日频时序为区间起点，默认近一年。"
        )
        out["end_date"] = (
            "结束日期 yyyy-MM-dd。财报类截面常与 start_date 同为季度末；"
            "日频行情不含当天实时数据，默认最近已收盘日。"
        )
        out["params"] = (
            "JSON 参数字典：根级键为全局参数（如 adjustmentType 复权方式），"
            "嵌套指标编码可单独覆盖（如 {\"adjustmentType\":\"3\",\"qte_close\":{\"adjustmentType\":\"2\"}}）。"
        )
    elif tool_name == "industry_indicator":
        out["keyword"] = (
            "检索关键词（如「空调」「新能源汽车销量」「黄金现货价」）。"
            "宏观默认中国；大宗商品避免单独用「价格」，宜用现货价/结算价/收盘价；"
            "产品销量不支持公司分品牌汇总，需拆成多个具体车型/品牌查询。"
        )
        out["indicator_ids"] = (
            "指标 ID 或名称，逗号分隔；提供后拉取时序数据。"
            "产品销量类数据仅本工具提供。"
        )
    return out
