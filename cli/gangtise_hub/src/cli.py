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

"""Hub CLI：调试用，列出域能力或打印 read_ref / 本地调用说明。"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from domains import DOMAINS, domain_tool_description
from package_loader import preload_all, try_load_domain
from router import route


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="gangtise-hub",
        description="Gangtise Hub：查看各域入口与叶子工具说明（方案 A）。",
    )
    parser.add_argument(
        "domain",
        nargs="?",
        choices=[d.tool_name for d in DOMAINS],
        help="域入口名，如 gangtise-data；省略则列出全部域",
    )
    parser.add_argument(
        "--action",
        choices=("list", "read_ref", "call"),
        default="list",
        help="与 MCP 工具的 action 一致（call 需 --name 与 --arguments）",
    )
    parser.add_argument("--name", help="叶子工具名（read_ref / call）")
    parser.add_argument(
        "--arguments",
        default="{}",
        help='call 参数 JSON，例如 \'{"securities":["比亚迪"]}\'',
    )
    args = parser.parse_args(argv)

    preload_all()

    if not args.domain:
        print("可用域入口（MCP tools/list 暴露域入口工具）：\n")
        for d in DOMAINS:
            rt = try_load_domain(d)
            status = f"{len(rt.specs)} 个叶子工具" if rt else "未加载兄弟包"
            print(f"  {d.tool_name:<20} {d.title}  [{status}]")
            print(f"    {domain_tool_description(d)[:120]}...")
            print()
        print("示例: gangtise-hub gangtise-data")
        print("      gangtise-hub gangtise-data --action read_ref --name quote")
        raise SystemExit(0)

    call_args = {
        "action": args.action,
        "name": args.name,
    }
    if args.action == "call":
        try:
            call_args["arguments"] = json.loads(args.arguments)
        except json.JSONDecodeError as e:
            print(f"无效 --arguments JSON: {e}", file=sys.stderr)
            raise SystemExit(2) from e

    text, invoke, _ = route(args.domain, call_args)
    if invoke is None:
        print(text)
        raise SystemExit(0)

    handler, filtered = invoke
    try:
        result = handler(**filtered)
    except Exception as e:
        print(f"调用失败: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    print(result if isinstance(result, str) else str(result))


if __name__ == "__main__":
    main()
