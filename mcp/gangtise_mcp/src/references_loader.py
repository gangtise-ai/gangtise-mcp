"""从 src/references/*.yaml 加载 MCP 工具描述与 JSON Schema。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

REFERENCES_DIR = Path(__file__).resolve().parent / "references"

_TYPE_MAP = {
    "string": "string",
    "str": "string",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "object": "object",
}


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    title: Optional[str] = None


def _append_desc(base: str, note: str) -> str:
    base = (base or "").strip()
    note = (note or "").strip()
    if not base:
        return note
    if not note:
        return base
    if note in base:
        return base
    return f"{base} {note}"


def _param_to_schema(param: Dict[str, Any]) -> Dict[str, Any]:
    """生成 JSON Schema；array/object 扁平化为 string（单层参数规范）。"""
    raw_type = str(param.get("type", "string")).lower()
    desc = str(param.get("description") or "")

    # 禁止嵌套 object / 原生 array，统一为根级基本类型
    if raw_type == "array":
        schema: Dict[str, Any] = {
            "type": "string",
            "description": _append_desc(
                desc,
                "Comma-separated string (flat schema; do not pass a JSON array).",
            ),
        }
    elif raw_type == "object":
        schema = {
            "type": "string",
            "description": _append_desc(
                desc,
                "JSON object serialized as a string (flat schema).",
            ),
        }
    else:
        schema = {"type": _TYPE_MAP.get(raw_type, raw_type)}
        if desc:
            schema["description"] = desc

    if "enum" in param:
        schema["enum"] = param["enum"]
    if "default" in param:
        default = param["default"]
        # 扁平化后 default 也须为基本类型
        if raw_type == "array" and isinstance(default, list):
            schema["default"] = ",".join(str(x) for x in default)
        elif raw_type == "object" and isinstance(default, dict):
            schema["default"] = json.dumps(default, ensure_ascii=False)
        else:
            schema["default"] = default
    return schema



def yaml_to_input_schema(parameters: Dict[str, Any]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for pname, pdef in (parameters or {}).items():
        if not isinstance(pdef, dict):
            pdef = {"type": "string", "description": str(pdef)}
        properties[pname] = _param_to_schema(pdef)
        if pdef.get("required") is True:
            required.append(pname)
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if s == "true":
        return True
    if s == "false":
        return False
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return json.loads(s)
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def _load_yaml_fallback(text: str) -> Dict[str, Any]:
    """极简 YAML 解析器，覆盖本仓库 references 的固定格式。"""
    lines = text.splitlines()
    root: Dict[str, Any] = {}
    stack: List[tuple[int, Any]] = [(-1, root)]
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        key_val = line.strip().split(":", 1)
        key = key_val[0].strip()
        val_part = key_val[1].strip() if len(key_val) > 1 else ""

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if val_part == "|":
            i += 1
            block: List[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= indent:
                    break
                block.append(nxt.strip())
                i += 1
            if isinstance(parent, dict):
                parent[key] = "\n".join(block).strip()
            continue

        if val_part == "":
            node: Dict[str, Any] = {}
            if isinstance(parent, dict):
                parent[key] = node
            stack.append((indent, node))
            i += 1
            continue

        if isinstance(parent, dict):
            parent[key] = _parse_scalar(val_part)
        i += 1
    return root


def _load_yaml_text(text: str) -> Dict[str, Any]:
    if yaml is not None:
        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    return _load_yaml_fallback(text)


def load_tool_spec(path: Path) -> ToolSpec:
    data = _load_yaml_text(path.read_text(encoding="utf-8"))
    name = str(data.get("name") or path.stem)
    description = str(data.get("description") or "").strip()
    if not description:
        description = name
    return ToolSpec(
        name=name,
        title=data.get("title"),
        description=description,
        input_schema=yaml_to_input_schema(data.get("parameters") or {}),
    )


def load_all_tool_specs(references_dir: Optional[Path] = None) -> List[ToolSpec]:
    root = references_dir or REFERENCES_DIR
    if not root.is_dir():
        return []
    specs: List[ToolSpec] = []
    for path in sorted(root.glob("*.yaml")):
        specs.append(load_tool_spec(path))
    return specs


def load_tool_spec_map(references_dir: Optional[Path] = None) -> Dict[str, ToolSpec]:
    return {spec.name: spec for spec in load_all_tool_specs(references_dir)}
