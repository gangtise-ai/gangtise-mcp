"""MCP callTool 错误语义（isError + JSON text）。"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, Set, Tuple, Union, get_args, get_origin

from mcp.types import TextContent

try:
    from mcp.types import CallToolResult
except ImportError:  # pragma: no cover
    CallToolResult = None  # type: ignore[misc, assignment]

ToolResult = Union[List[Any], Any]


def tool_error_text(message: str, code: str = "TOOL_ERROR") -> str:
    return json.dumps({"code": code, "message": message}, ensure_ascii=False)


def tool_error(
    message: str,
    code: str = "TOOL_ERROR",
) -> Any:
    """返回带 isError=True 的 CallToolResult（若 SDK 支持），否则退回 TextContent 列表。"""
    content = [TextContent(type="text", text=tool_error_text(message, code))]
    if CallToolResult is not None:
        return CallToolResult(content=content, isError=True)
    return content


def coerce_json_object_arg(value: Any) -> Any:
    """扁平化后 object 参数以 JSON 字符串传入时，还原为 dict。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"无效 JSON 对象字符串: {e}") from e
        if not isinstance(parsed, dict):
            raise ValueError("JSON 字符串须解析为 object")
        return parsed
    raise ValueError(f"期望 object 或 JSON 字符串，收到 {type(value).__name__}")


def _is_list_type(annotation: Any) -> bool:
    if annotation is inspect.Parameter.empty:
        return False
    origin = get_origin(annotation)
    if origin in (list, List, tuple, Tuple, set, Set):
        return True
    if annotation in (list, tuple, set):
        return True
    return False


def _is_tuple_type(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin is tuple or annotation is tuple


def _coerce_value(value: Any, annotation: Any) -> Any:
    """按 handler 注解把扁平化 string 还原为 list/bool/number 等。"""
    if value is None:
        return None
    if annotation is inspect.Parameter.empty:
        return value

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is not None and type(None) in args:
        inner = next((a for a in args if a is not type(None)), Any)
        return _coerce_value(value, inner)

    if _is_list_type(annotation) or (origin in (list, List, tuple, Tuple) and args):
        if isinstance(value, str):
            items = [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = []
            for item in value:
                if isinstance(item, str) and ("," in item or "，" in item):
                    items.extend(
                        x.strip() for x in item.replace("，", ",").split(",") if x.strip()
                    )
                else:
                    items.append(item)
        else:
            items = [value]
        if _is_tuple_type(annotation) or annotation is tuple:
            return tuple(items)
        return items

    if annotation is int or annotation is float:
        return annotation(value)
    if annotation is bool and isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "y", "on")
    return value


def coerce_tool_kwargs(handler: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """MCP 扁平化 schema 后，按 handler 类型注解还原参数（如 period: "Q0" → ["Q0"]）。"""
    sig = inspect.signature(handler)
    out: Dict[str, Any] = {}
    for name, value in kwargs.items():
        if value is None:
            continue
        param = sig.parameters.get(name)
        ann = param.annotation if param else inspect.Parameter.empty
        if ann is inspect.Parameter.empty and isinstance(value, str):
            if name == "params" and value.strip().startswith(("{", "[")):
                try:
                    out[name] = json.loads(value)
                    continue
                except json.JSONDecodeError:
                    pass
        out[name] = _coerce_value(value, ann)
    return out
