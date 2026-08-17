#!/usr/bin/env python3
"""uvx 远程安装 smoke：不依赖本地 monorepo 源码，拉取 git 子目录后执行 CLI。

用法：
  python3 test/uvx.py
  python3 test/uvx.py --from-git https://gitee.com/gangtise/gangtise-mcp
  python3 test/uvx.py --tool list
  python3 test/uvx.py --tool security -- -k 茅台
  python3 test/uvx.py --mcp-only   # 仅拉 mcp 包并打印 --help（stdio 入口）

默认（Gitee / 中文文档一致）：
  --with  git+...#subdirectory=mcp/gangtise_mcp
  --from  git+...#subdirectory=cli/gangtise_mcp
  命令  gangtise

环境变量：
  GTS_ACCESS_KEY / GTS_SECRET_KEY  （调用业务工具时需要）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import List

DEFAULT_GIT = "https://gitee.com/gangtise/gangtise-mcp"
PYPI_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_MCP_SUBDIR = "mcp/gangtise_mcp"
DEFAULT_CLI_SUBDIR = "cli/gangtise_mcp"
DEFAULT_CMD = "gangtise"
DEFAULT_MCP_CMD = "gangtise-mcp"


def _ok(name: str, detail: str = "") -> None:
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str) -> None:
    print(f"  FAIL  {name} — {detail}", file=sys.stderr)


def _run(argv: List[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    print("  $ " + " ".join(argv))
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=os.environ.copy(),
    )


def _git_ref(git: str, subdirectory: str) -> str:
    return f"git+{git.rstrip('/')}#subdirectory={subdirectory}"


def main() -> int:
    parser = argparse.ArgumentParser(description="uvx 远程 CLI / MCP smoke 测试")
    parser.add_argument("--from-git", default=DEFAULT_GIT, help=f"git 仓库 URL（默认 {DEFAULT_GIT}）")
    parser.add_argument("--mcp-subdir", default=DEFAULT_MCP_SUBDIR, help="mcp 包 subdirectory")
    parser.add_argument("--cli-subdir", default=DEFAULT_CLI_SUBDIR, help="cli 包 subdirectory")
    parser.add_argument("--cmd", default=DEFAULT_CMD, help="CLI 入口命令（默认 gangtise）")
    parser.add_argument("--mcp-cmd", default=DEFAULT_MCP_CMD, help="MCP stdio 入口（默认 gangtise-mcp）")
    parser.add_argument("--index", default=PYPI_INDEX, help="uv 默认 PyPI 镜像")
    parser.add_argument(
        "--mcp-only",
        action="store_true",
        help="只测 mcp 包 uvx --help，不测 CLI",
    )
    parser.add_argument(
        "--tool",
        default="list",
        help="CLI 子命令（默认 list；设为空字符串则只跑 --help）",
    )
    parser.add_argument(
        "tool_args",
        nargs=argparse.REMAINDER,
        help="传给 --tool 的参数（写在 -- 之后）",
    )
    args = parser.parse_args()

    if not shutil.which("uvx") and not shutil.which("uv"):
        print("未找到 uv/uvx，请先安装：https://docs.astral.sh/uv/", file=sys.stderr)
        return 2

    uvx = "uvx" if shutil.which("uvx") else None
    if uvx is None:
        # uv 自带 uvx 子命令较少见；优先要求 uvx
        print("未找到 uvx 可执行文件", file=sys.stderr)
        return 2

    mcp_ref = _git_ref(args.from_git, args.mcp_subdir)
    cli_ref = _git_ref(args.from_git, args.cli_subdir)
    print(f"from_git={args.from_git}")
    print(f"mcp_ref={mcp_ref}")
    if not args.mcp_only:
        print(f"cli_ref={cli_ref}")
    print()

    if args.mcp_only:
        print(f"[1] uvx ... {args.mcp_cmd} --help")
        proc = _run(
            [
                uvx,
                "--default-index",
                args.index,
                "--from",
                mcp_ref,
                args.mcp_cmd,
                "--help",
            ],
            timeout=900,
        )
        if proc.returncode != 0:
            _fail("mcp --help", (proc.stderr or proc.stdout or "")[:1000])
            return 1
        _ok("mcp --help")
        print()
        print("全部通过。")
        return 0

    base = [
        uvx,
        "--default-index",
        args.index,
        "--with",
        mcp_ref,
        "--from",
        cli_ref,
        args.cmd,
    ]

    print(f"[1] uvx ... {args.cmd} --help")
    proc = _run([*base, "--help"], timeout=900)
    if proc.returncode != 0:
        _fail("--help", (proc.stderr or proc.stdout or "")[:1000])
        return 1
    _ok("--help")

    tool = (args.tool or "").strip()
    if not tool:
        print()
        print("全部通过。")
        return 0

    tool_argv = list(args.tool_args)
    if tool_argv and tool_argv[0] == "--":
        tool_argv = tool_argv[1:]

    print(f"[2] uvx ... {args.cmd} {tool} {' '.join(tool_argv)}".rstrip())
    proc = _run([*base, tool, *tool_argv], timeout=900)
    if proc.returncode != 0:
        _fail(tool, (proc.stderr or proc.stdout or "")[:1000])
        return 1
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    _ok(tool, f"{len(lines)} lines")

    print()
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
