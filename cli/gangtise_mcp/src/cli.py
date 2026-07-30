from __future__ import annotations
import sys
from pathlib import Path

def _ensure_layer_paths() -> None:
    here = Path(__file__).resolve().parent
    if here.name == "src":
        pkg = here.parent.name
        root = here.parents[2]  # mcps
        mcp_src = root / "mcp" / pkg / "src"
    else:
        pkg = here.name
        root = here.parents[1] if here.parent.name == "cli" else here.parent
        mcp_src = root / "mcp" / pkg / "src"
    s = str(mcp_src)
    if mcp_src.is_dir() and s not in sys.path:
        sys.path.insert(0, s)
    # hub / full also need sibling domain sources
    if pkg in ("gangtise_hub", "gangtise_mcp") and mcp_src.is_dir():
        mcp_root = mcp_src.parent.parent
        for dom in (
            "gangtise_agent",
            "gangtise_data",
            "gangtise_file",
            "gangtise_kb",
            "gangtise_private",
            "gangtise_pdf",
        ):
            ds = mcp_root / dom / "src"
            ss = str(ds)
            if ds.is_dir() and ss not in sys.path:
                sys.path.insert(0, ss)


_ensure_layer_paths()

"""Gangtise 命令行入口 — 与 MCP 工具同源，参数说明来自 references/*.yaml。"""
import argparse
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, get_args, get_origin

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from references_loader import ToolSpec
from tools_registry import INTERNAL_PARAMS, TOOL_HANDLERS
from tool_catalog import load_catalog
from authorization import (
    get_authorization_path,
    is_auth_configured,
    refresh_authorization,
    save_credentials,
    clear_credentials,
)


def load_tool_spec_map() -> Dict[str, ToolSpec]:
    return load_catalog().spec_map

# 与 skill 脚本常用短选项对齐
_SHORT_FLAGS: Dict[str, str] = {
    "start_date": "sd",
    "end_date": "ed",
    "start_time": "st",
    "end_time": "et",
    "keyword": "k",
    "limit": "l",
    "params": "p",
    "theme_id": "t",
    "concepts": "c",
}

# 额外长选项别名（kebab-case -> 参数名）
_LONG_ALIASES: Dict[str, str] = {
    "all-market": "all_market_markets",
    "all-market-markets": "all_market_markets",
    "data-type": "data_type",
    "adjust-mode": "adjust_mode",
    "indicator-codes": "indicator_codes",
    "indicator-ids": "indicator_ids",
    "query-type": "query_type",
    "theme-id": "theme_id",
    "page-from": "page_from",
    "page-size": "page_size",
    "output-limit": "output_limit",
    "sector-id": "sector_id",
    "file-id": "file_id",
    "file-ids": "file_ids",
}

_TOOL_GROUPS: List[Tuple[str, List[str]]] = [
    ("Data 数据", ['block_constituents', 'company_indicator', 'concept', 'earning_forecast', 'financial', 'fund_flow', 'industry_indicator', 'main_business', 'quote', 'security', 'shareholder', 'valuation']),
    ("File 文件", ['report', 'summary', 'opinion', 'announcement', 'foreign_report', 'foreign_opinion', 'official_account', 'management_discuss', 'qa', 'report_image', 'investment_calendar', 'get_file', 'get_chiefs', 'get_institutions', 'get_industries', 'get_regions', 'get_announcement_types']),
    ("Agent 研报", ['stock_one_pager', 'investment_logic', 'peer_comparison', 'earnings_review', 'viewpoint_debate', 'theme_tracking', 'research_outline', 'stock_one_line_summary', 'hot_topic', 'security_clue']),
    ("KB 知识库", ['kb']),
    ("Private 私有", ['private_record', 'private_meeting', 'private_cloud', 'stockpool', 'wechat_message']),
    ("PDF 解析", ['pdf_parse']),
]



def _snake_to_kebab(name: str) -> str:
    return name.replace("_", "-")


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _check_auth_configured() -> Optional[str]:
    if is_auth_configured():
        return None
    path = get_authorization_path()
    return (
        "未配置 Gangtise 授权（AccessKey / SecretKey）\n"
        "请前往 https://open-platform.gangtise.com/ 进行账号登陆/申请并获取凭证\n"
        "登陆后在`我的账号`->`账号列表`页面最下方查看 Access Key 和 Secret Key\n"
        f"配置方式：gangtise configure --access-key <AK> --secret-key <SK>"
        f"（凭证保存到 {path}），或设置环境变量 GTS_ACCESS_KEY / GTS_SECRET_KEY）"
    )


def _filter_arguments(handler: Callable[..., Any], arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    sig = inspect.signature(handler)
    allowed = {
        name
        for name, p in sig.parameters.items()
        if name not in INTERNAL_PARAMS and p.kind != p.VAR_KEYWORD
    }
    extra = set(arguments) - allowed
    if extra:
        valid = ", ".join(sorted(allowed))
        bad = ", ".join(sorted(extra))
        return {}, f"未知参数: {bad}。有效参数: {valid}"
    return {k: v for k, v in arguments.items() if k in allowed}, None


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
            items = [x.strip() for x in value.split(",") if x.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = []
            for item in value:
                if isinstance(item, str) and "," in item:
                    items.extend(x.strip() for x in item.split(",") if x.strip())
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


def _coerce_kwargs(handler: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    sig = inspect.signature(handler)
    out: Dict[str, Any] = {}
    for name, value in kwargs.items():
        if value is None:
            continue
        param = sig.parameters.get(name)
        ann = param.annotation if param else inspect.Parameter.empty
        if ann is inspect.Parameter.empty and isinstance(value, str):
            # object / JSON 参数
            if name == "params" and value.strip().startswith(("{", "[")):
                try:
                    out[name] = json.loads(value)
                    continue
                except json.JSONDecodeError:
                    pass
        out[name] = _coerce_value(value, ann)
    return out


def _parse_object_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"无效 JSON: {e}") from e


def _split_csv(value: str) -> List[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _add_param_argument(
    parser: argparse.ArgumentParser,
    pname: str,
    pschema: Dict[str, Any],
    *,
    required_yaml: bool,
    default: Any,
) -> None:
    kebab = _snake_to_kebab(pname)
    flags = [f"--{kebab}"]
    short = _SHORT_FLAGS.get(pname)
    if short:
        flags.insert(0, f"-{short}")

    for alias, target in _LONG_ALIASES.items():
        if target == pname and f"--{alias}" not in flags:
            flags.append(f"--{alias}")

    ptype = str(pschema.get("type", "string")).lower()
    help_text = str(pschema.get("description") or pname).strip()
    kwargs: Dict[str, Any] = {"dest": pname, "help": help_text}

    if ptype == "boolean":
        if default is True:
            parser.add_argument(*flags, action="store_false", help=help_text)
        else:
            parser.add_argument(*flags, action="store_true", help=help_text)
        return

    if ptype == "integer":
        kwargs["type"] = int
    elif ptype == "number":
        kwargs["type"] = float
    elif ptype == "array":
        kwargs["nargs"] = "+"
        kwargs["type"] = str
        kwargs["metavar"] = "ITEM"
        kwargs["help"] = f"{help_text}（可多次传入或逗号分隔）"
    elif ptype == "object":
        kwargs["type"] = _parse_object_json
        kwargs["metavar"] = "JSON"
    else:
        kwargs["type"] = str

    if default is not inspect.Parameter.empty and default is not None:
        kwargs["default"] = default
    elif required_yaml:
        kwargs["required"] = False  # 运行前再校验，便于 --help 不强制占位

    parser.add_argument(*flags, **kwargs)


def _build_tool_parser(
    subparsers: argparse._SubParsersAction,
    tool_name: str,
    handler: Callable[..., Any],
    spec: Optional[ToolSpec],
) -> None:
    description = spec.description if spec else tool_name
    summary = _first_line(description) or tool_name
    parser = subparsers.add_parser(
        tool_name,
        help=summary,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    schema = (spec.input_schema if spec else {}) or {}
    properties: Dict[str, Any] = schema.get("properties") or {}
    required_set = set(schema.get("required") or [])
    sig = inspect.signature(handler)

    seen: Set[str] = set()
    for pname, pschema in properties.items():
        if pname in INTERNAL_PARAMS:
            continue
        param = sig.parameters.get(pname)
        default = param.default if param and param.default is not inspect.Parameter.empty else None
        if not isinstance(pschema, dict):
            pschema = {"type": "string", "description": str(pschema)}
        _add_param_argument(
            parser,
            pname,
            pschema,
            required_yaml=pname in required_set,
            default=default,
        )
        seen.add(pname)

    for pname, param in sig.parameters.items():
        if pname in INTERNAL_PARAMS or pname in seen:
            continue
        if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            continue
        default = param.default if param.default is not inspect.Parameter.empty else None
        _add_param_argument(
            parser,
            pname,
            {"type": "string", "description": pname},
            required_yaml=default is inspect.Parameter.empty,
            default=default,
        )


def _build_epilog(spec_map: Dict[str, ToolSpec]) -> str:
    lines = ["子命令一览（gangtise <命令> --help 查看参数）:", ""]
    listed: Set[str] = set()
    for title, names in _TOOL_GROUPS:
        items = [n for n in names if n in TOOL_HANDLERS]
        if not items:
            continue
        lines.append(f"  {title}:")
        for name in items:
            spec = spec_map.get(name)
            summary = _first_line(spec.description) if spec else name
            lines.append(f"    {name:<28} {summary}")
            listed.add(name)
        lines.append("")
    other = sorted(set(TOOL_HANDLERS) - listed - {"list"})
    if other:
        lines.append("  其他:")
        for name in other:
            spec = spec_map.get(name)
            summary = _first_line(spec.description) if spec else name
            lines.append(f"    {name:<28} {summary}")
        lines.append("")
    lines.append("示例:")
    lines.append("  gangtise configure --access-key <AK> --secret-key <SK>")
    lines.append("  gangtise quote --securities 比亚迪")
    lines.append("  gangtise security -k 比亚迪")
    lines.append("  gangtise company-indicator -k 收盘价")
    lines.append("  gangtise list")
    return "\n".join(lines)


def _cmd_list(spec_map: Dict[str, ToolSpec]) -> int:
    print(_build_epilog(spec_map).replace("子命令一览（gangtise <命令> --help 查看参数）:", "可用命令:"))
    return 0


def _validate_required(spec: Optional[ToolSpec], kwargs: Dict[str, Any]) -> Optional[str]:
    schema = (spec.input_schema if spec else {}) or {}
    missing = [p for p in schema.get("required") or [] if kwargs.get(p) in (None, "", [])]
    if missing:
        return f"缺少必填参数: {', '.join(missing)}"
    return None


def _cmd_configure(args: argparse.Namespace) -> int:
    path = get_authorization_path()

    if args.show:
        if is_auth_configured():
            print(f"已配置授权，凭证文件: {path}")
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ak = str(data.get("accessKey") or "")
                    if ak:
                        masked = ak[:4] + "*" * max(0, len(ak) - 8) + ak[-4:] if len(ak) > 8 else "****"
                        print(f"  accessKey: {masked}")
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
        else:
            print(f"未配置授权。运行 gangtise configure --access-key <AK> --secret-key <SK>")
        return 0

    ak = (args.access_key or "").strip()
    sk = (args.secret_key or "").strip()
    if args.from_env:
        ak = os.getenv("GTS_ACCESS_KEY", ak)
        sk = os.getenv("GTS_SECRET_KEY", sk)

    if not ak:
        ak = input("GTS Access Key: ").strip()
    if not sk:
        import getpass

        sk = getpass.getpass("GTS Secret Key: ").strip()

    try:
        saved = save_credentials(ak, sk, path)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    session = refresh_authorization(force=True)
    if not session:
        print("凭证已保存，但登录验证失败，请检查 Access Key / Secret Key。", file=sys.stderr)
        return 1

    print(f"授权配置已保存: {saved}")
    return 0



def _cmd_uninstall(args: argparse.Namespace) -> int:
    path = get_authorization_path()
    removed = clear_credentials(path)
    if removed:
        print(f"已删除本地凭证: {removed}")
    else:
        print(f"本地无凭证文件: {path}")
    if os.getenv("GTS_ACCESS_KEY") or os.getenv("GTS_SECRET_KEY"):
        print(
            "提示：当前环境仍设置了 GTS_ACCESS_KEY / GTS_SECRET_KEY，"
            "优先级高于本地文件；如需完全退出请 unset 它们。",
            file=sys.stderr,
        )
    return 0


def _build_parser(spec_map: Dict[str, ToolSpec]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gangtise",
        description=(
            "Gangtise 全量命令行工具（与 gangtise_mcp / 各域叶子工具同源）。\n"
            "与 MCP 服务使用同一套工具实现；参数名使用 kebab-case（如 --start-date），"
            "亦支持常用短选项（如 -k、-sd、-ed）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_build_epilog(spec_map),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="gangtise-mcp-cli 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="tool", metavar="<命令>")
    cfg = subparsers.add_parser(
        "configure",
        help="保存 Access Key / Secret Key 到本地配置文件",
        description=(
            "将 Gangtise API 凭证保存到本地（默认 ~/.config/gangtise/authorization），"
            "之后无需每次 export 环境变量。环境变量 GTS_ACCESS_KEY / GTS_SECRET_KEY 优先级更高。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cfg.add_argument("--access-key", metavar="AK", help="Gangtise Access Key")
    cfg.add_argument("--secret-key", metavar="SK", help="Gangtise Secret Key")
    cfg.add_argument(
        "--from-env",
        action="store_true",
        help="从当前环境的 GTS_ACCESS_KEY / GTS_SECRET_KEY 读取并写入配置文件",
    )
    cfg.add_argument("--show", action="store_true", help="查看是否已配置（不显示完整密钥）")

    subparsers.add_parser(
        "uninstall",
        help="删除本地保存的 Access Key / Secret Key 凭证文件",
        description=(
            "删除本地凭证文件（默认 ~/.config/gangtise/authorization），并清除内存中的登录缓存。"
            "不会修改当前 shell 的 GTS_ACCESS_KEY / GTS_SECRET_KEY 环境变量。"
        ),
    )

    subparsers.add_parser(
        "list",
        help="列出全部子命令及简介",
        description="列出全部可用子命令及一行简介。",
    )

    for tool_name, handler in sorted(TOOL_HANDLERS.items()):
        _build_tool_parser(subparsers, tool_name, handler, spec_map.get(tool_name))

    return parser


def _normalize_argv(argv: Optional[List[str]]) -> List[str]:
    out = list(argv if argv is not None else sys.argv[1:])
    if out and out[0] not in ("-h", "--help", "--version", "list", "configure", "uninstall") and "-" in out[0]:
        out[0] = out[0].replace("-", "_")
    return out


def main(argv: Optional[List[str]] = None) -> None:
    argv = _normalize_argv(argv)
    spec_map = load_tool_spec_map()
    parser = _build_parser(spec_map)
    args = parser.parse_args(argv)

    if not getattr(args, "tool", None):
        parser.print_help()
        raise SystemExit(0)

    if args.tool == "configure":
        raise SystemExit(_cmd_configure(args))

    if args.tool == "uninstall":
        raise SystemExit(_cmd_uninstall(args))

    if args.tool == "list":
        raise SystemExit(_cmd_list(spec_map))

    handler = TOOL_HANDLERS.get(args.tool)
    if handler is None:
        parser.error(f"未知命令: {args.tool}")

    auth_err = _check_auth_configured()
    if auth_err:
        print(auth_err, file=sys.stderr)
        raise SystemExit(1)

    spec = spec_map.get(args.tool)
    raw_kwargs = {k: v for k, v in vars(args).items() if k != "tool" and v is not None}
    kwargs = _coerce_kwargs(handler, raw_kwargs)
    filtered, param_err = _filter_arguments(handler, kwargs)
    if param_err:
        print(param_err, file=sys.stderr)
        raise SystemExit(2)

    req_err = _validate_required(spec, filtered)
    if req_err:
        print(req_err, file=sys.stderr)
        raise SystemExit(2)

    try:
        result = handler(**filtered)
    except TypeError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    except Exception as e:
        print(f"调用失败: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    if result is None:
        return
    print(result if isinstance(result, str) else str(result))


if __name__ == "__main__":
    main()
