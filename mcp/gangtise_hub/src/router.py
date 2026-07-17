"""域入口路由：list / read_ref / call（方案 A）。"""
from __future__ import annotations

import inspect
import json
from typing import Any, Dict, Optional, Tuple

from domains import DOMAIN_BY_TOOL, DomainDef, ROUTER_ACTIONS
from package_loader import DomainRuntime, load_error, try_load_domain
from references_loader import ToolSpec
from url_whitelist import get_white_list, is_tool_allowed, tool_denied_reason


def _allowed_specs(runtime: DomainRuntime):
    wl = get_white_list()
    return [s for s in runtime.specs if is_tool_allowed(s.name, wl)]


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _schema_required(spec: ToolSpec) -> list:
    return list((spec.input_schema or {}).get("required") or [])


def _example_value(pname: str, pdef: Dict[str, Any]) -> Any:
    t = str(pdef.get("type") or "string")
    if t == "integer":
        return 10
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if pname == "securities":
        return "贵州茅台"
    if pname in ("keyword", "query", "q"):
        return "比亚迪"
    if "date" in pname:
        return "2024-01-01"
    return "..."


def _example_arguments(spec: ToolSpec) -> Dict[str, Any]:
    """根据 schema 生成轻量示例参数（供模型参考，非真实必填全集）。"""
    props = (spec.input_schema or {}).get("properties") or {}
    required = list(_schema_required(spec))
    preferred = [
        "securities",
        "keyword",
        "query",
        "q",
        "file_ids",
        "start_date",
        "end_date",
        "limit",
        "mode",
    ]
    ordered = required + [n for n in preferred if n in props and n not in required]
    if not ordered:
        ordered = list(props.keys())[:3]

    example: Dict[str, Any] = {}
    for name in ordered:
        if name not in props or name in example:
            continue
        pdef = props[name]
        if not isinstance(pdef, dict):
            pdef = {"type": "string"}
        example[name] = _example_value(name, pdef)
        if len(example) >= 3:
            break
    return example


def _call_example_json(domain_tool: str, leaf: str, arguments: Dict[str, Any]) -> str:
    payload = {
        "action": "call",
        "name": leaf,
        "arguments_json": json.dumps(arguments, ensure_ascii=False),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_list(domain: DomainDef, runtime: Optional[DomainRuntime]) -> str:
    lines = [
        f"# {domain.tool_name}",
        "",
        f"**{domain.title}**",
        "",
        domain.summary,
        "",
        f"**适用场景**：{domain.when_to_use}",
        "",
        "## 调用约定",
        "",
        "本入口为路由工具（方案 A）。请按下列 `action` 使用：",
        "",
        "| action | 说明 |",
        "|--------|------|",
        "| `list`（默认） | 本页：下级叶子工具目录 |",
        "| `read_ref` | 读取 `name` 对应叶子工具的完整参数说明（等同 `references/<name>.yaml`） |",
        "| `call` | 执行叶子工具：`name` + `arguments_json` |",
        "",
        "```json",
        json.dumps(
            {"action": "read_ref", "name": "<leaf_tool>"},
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "```json",
        json.dumps(
            {
                "action": "call",
                "name": "<leaf_tool>",
                "arguments_json": "{\"...\":\"...\"}",
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
    ]

    if runtime is None:
        err = load_error(domain.tool_name) or "未知错误"
        lines += [
            "## 加载失败",
            "",
            f"无法加载域 `{domain.tool_name}`：{err}",
            "",
            "请先运行仓库根目录 `sync_skills_to_mcp.py`，确保本包已内嵌五域脚本与 references。",
            "",
        ]
        return "\n".join(lines)

    allowed = _allowed_specs(runtime)
    lines += [
        "## 下级工具",
        "",
        f"共 **{len(allowed)}** 个叶子工具（本包内嵌 `references/*.yaml`，域 `{domain.tool_name}`；已按权限过滤）。",
        "",
    ]

    for spec in allowed:
        summary = _first_line(spec.description) or spec.name
        req = _schema_required(spec)
        req_s = ", ".join(f"`{x}`" for x in req) if req else "无强制必填（见 read_ref）"
        example_args = _example_arguments(spec)
        lines += [
            f"### `{spec.name}`",
            "",
            summary,
            "",
            f"- 参数说明路径语义：`{domain.tool_name}/references/{spec.name}.yaml`",
            f"- 常见必填提示：{req_s}",
            f"- 完整参数：对本工具调用 `action=read_ref, name={spec.name}`",
            "",
            "调用示例：",
            "",
            "```json",
            _call_example_json(domain.tool_name, spec.name, example_args),
            "```",
            "",
        ]

    lines += [
        "## 与其它入口的区别",
        "",
        "| 入口 | 侧重 |",
        "|------|------|",
        "| `gangtise-data` | 结构化数值表 / 题材画像 |",
        "| `gangtise-file` | 文件索引与下载 |",
        "| `gangtise-kb` | 语义片段阅读 |",
        "| `gangtise-agent` | Agent 投研结论文本 |",
        "| `gangtise-private` | 个人私有数据 |",
        "",
    ]
    return "\n".join(lines)


def render_read_ref(domain: DomainDef, runtime: DomainRuntime, leaf: str) -> str:
    denied = tool_denied_reason(leaf)
    if denied:
        return f"无权限查看工具 `{leaf}`：{denied}"
    spec = runtime.spec_map.get(leaf)
    if spec is None:
        known = ", ".join(s.name for s in _allowed_specs(runtime)) or "(无)"
        return f"未知叶子工具: `{leaf}`。可用: {known}"

    props = (spec.input_schema or {}).get("properties") or {}
    required = set(_schema_required(spec))
    lines = [
        f"# {domain.tool_name} / references / {spec.name}",
        "",
        f"**工具名**：`{spec.name}`",
        "",
        spec.description or "(无描述)",
        "",
        "## 参数",
        "",
    ]
    if not props:
        lines.append("（无参数）")
        lines.append("")
    else:
        lines += [
            "| 参数 | 类型 | 必填 | 说明 |",
            "|------|------|------|------|",
        ]
        for pname, pdef in props.items():
            if not isinstance(pdef, dict):
                pdef = {"type": "string", "description": str(pdef)}
            typ = str(pdef.get("type") or "string")
            req = "是" if pname in required else "否"
            desc = str(pdef.get("description") or "").replace("\n", " ")
            if "enum" in pdef:
                desc = f"{desc} enum={pdef['enum']}".strip()
            if "default" in pdef:
                desc = f"{desc} default={pdef['default']!r}".strip()
            lines.append(f"| `{pname}` | {typ} | {req} | {desc} |")
        lines.append("")

    example_args = _example_arguments(spec)
    lines += [
        "## 调用示例",
        "",
        f"对本入口工具 `{domain.tool_name}` 传入：",
        "",
        "```json",
        _call_example_json(domain.tool_name, spec.name, example_args),
        "```",
        "",
        "## JSON Schema",
        "",
        "```json",
        json.dumps(spec.input_schema or {}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def filter_arguments(
    handler: Any,
    arguments: Dict[str, Any],
    internal_params: frozenset,
) -> Tuple[Dict[str, Any], Optional[str]]:
    sig = inspect.signature(handler)
    allowed = {
        name
        for name, p in sig.parameters.items()
        if name not in internal_params and p.kind != p.VAR_KEYWORD
    }
    extra = set(arguments or {}) - allowed
    if extra:
        valid = ", ".join(sorted(allowed))
        bad = ", ".join(sorted(extra))
        return {}, f"未知参数: {bad}。有效参数: {valid}"
    filtered = {k: v for k, v in (arguments or {}).items() if k in allowed}
    return filtered, None


def validate_required(spec: Optional[ToolSpec], kwargs: Dict[str, Any]) -> Optional[str]:
    if spec is None:
        return None
    missing = [p for p in _schema_required(spec) if kwargs.get(p) in (None, "", [])]
    if missing:
        return f"缺少必填参数: {', '.join(missing)}。可用 action=read_ref 查看完整说明。"
    return None


def route(
    domain_tool: str,
    arguments: Optional[Dict[str, Any]],
) -> Tuple[str, Optional[Any], Optional[DomainRuntime]]:
    """
    返回 (text_or_error, invoke_tuple_or_None, runtime)。
    invoke_tuple = (handler, filtered_kwargs) 表示需执行 call；
    若仅返回文档则 invoke 为 None。
    """
    args = dict(arguments or {})
    domain = DOMAIN_BY_TOOL.get(domain_tool)
    if domain is None:
        return f"未知域入口: {domain_tool}", None, None

    action = str(args.get("action") or "list").strip().lower()
    if action not in ROUTER_ACTIONS:
        return (
            f"未知 action: {action}。可选: {', '.join(ROUTER_ACTIONS)}",
            None,
            None,
        )

    runtime = try_load_domain(domain)

    if action == "list":
        return render_list(domain, runtime), None, runtime

    if runtime is None:
        err = load_error(domain.tool_name) or "兄弟包未加载"
        return f"无法执行 {action}：{err}", None, None

    leaf = str(args.get("name") or "").strip()
    if not leaf:
        return "action=read_ref/call 时必须提供 name（叶子工具名）。先用 action=list 查看目录。", None, runtime

    if action == "read_ref":
        return render_read_ref(domain, runtime, leaf), None, runtime

    # call
    denied = tool_denied_reason(leaf)
    if denied:
        return f"无权限调用工具 `{leaf}`：{denied}", None, runtime
    handler = runtime.handlers.get(leaf)
    if handler is None:
        known = ", ".join(s.name for s in _allowed_specs(runtime)) or "(无)"
        return f"未知叶子工具: `{leaf}`。可用: {known}", None, runtime

    call_args = args.get("arguments_json")
    if call_args is None:
        call_args = args.get("arguments")
    if call_args is None:
        call_args = {}
    if isinstance(call_args, str):
        text = call_args.strip()
        if not text:
            call_args = {}
        else:
            try:
                call_args = json.loads(text)
            except json.JSONDecodeError as e:
                return f"arguments_json 不是合法 JSON: {e}", None, runtime
    if not isinstance(call_args, dict):
        return "arguments_json 必须是 JSON object 字符串。", None, runtime

    filtered, param_err = filter_arguments(handler, call_args, runtime.internal_params)
    if param_err:
        return param_err, None, runtime

    req_err = validate_required(runtime.spec_map.get(leaf), filtered)
    if req_err:
        return req_err, None, runtime

    return "", (handler, filtered), runtime
