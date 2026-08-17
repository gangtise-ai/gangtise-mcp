#!/usr/bin/env python3
"""本地源码 CLI smoke：对 cli/gangtise_mcp 执行 uv sync + gangtise list / --help / 可选工具调用。

用法（在 mcps/ 下）：
  python3 test/cli.py
  python3 test/cli.py --cli-dir cli/gangtise_file --cmd gangtise-file
  python3 test/cli.py --tool security -k 茅台
  python3 test/cli.py --skip-sync

环境变量：
  GTS_ACCESS_KEY / GTS_SECRET_KEY  （调用 --tool 时需要）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MCPS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI_DIR = MCPS_ROOT / "cli" / "gangtise_mcp"
DEFAULT_CMD = "gangtise"
PYPI_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _ok(name: str, detail: str = "") -> None:
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, detail: str) -> None:
    print(f"  FAIL  {name} — {detail}", file=sys.stderr)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=os.environ.copy(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 CLI（uv run）smoke 测试")
    parser.add_argument(
        "--cli-dir",
        default=str(DEFAULT_CLI_DIR),
        help=f"CLI 包目录（默认 {DEFAULT_CLI_DIR}）",
    )
    parser.add_argument("--cmd", default=DEFAULT_CMD, help="入口命令名（默认 gangtise）")
    parser.add_argument("--skip-sync", action="store_true", help="跳过 uv sync")
    parser.add_argument(
        "--tool",
        default="",
        help="可选：在 list/--help 通过后额外调用的工具名，如 security / quote",
    )
    parser.add_argument(
        "tool_args",
        nargs=argparse.REMAINDER,
        help="传给 --tool 的参数（写在 -- 之后，如 -- -k 茅台）",
    )
    args = parser.parse_args()

    if not shutil.which("uv"):
        print("未找到 uv，请先安装：https://docs.astral.sh/uv/", file=sys.stderr)
        return 2

    cli_dir = Path(args.cli_dir).expanduser().resolve()
    if not (cli_dir / "pyproject.toml").is_file():
        _fail("cli-dir", f"缺少 pyproject.toml: {cli_dir}")
        return 2

    print(f"cli_dir={cli_dir}")
    print(f"cmd={args.cmd}")
    print()

    if not args.skip_sync:
        print("[1] uv sync")
        proc = _run(
            ["uv", "sync", "--default-index", PYPI_INDEX],
            cwd=cli_dir,
            timeout=600,
        )
        if proc.returncode != 0:
            _fail("uv sync", (proc.stderr or proc.stdout or "")[:800])
            return 1
        _ok("uv sync")
    else:
        print("[1] uv sync (skipped)")

    print(f"[2] uv run {args.cmd} --help")
    proc = _run(["uv", "run", args.cmd, "--help"], cwd=cli_dir, timeout=120)
    if proc.returncode != 0:
        _fail("--help", (proc.stderr or proc.stdout or "")[:800])
        return 1
    _ok("--help")

    print(f"[3] uv run {args.cmd} list")
    proc = _run(["uv", "run", args.cmd, "list"], cwd=cli_dir, timeout=180)
    if proc.returncode != 0:
        _fail("list", (proc.stderr or proc.stdout or "")[:800])
        return 1
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    _ok("list", f"{len(lines)} lines")

    tool = (args.tool or "").strip()
    if tool:
        tool_argv = list(args.tool_args)
        if tool_argv and tool_argv[0] == "--":
            tool_argv = tool_argv[1:]
        print(f"[4] uv run {args.cmd} {tool} {' '.join(tool_argv)}".rstrip())
        proc = _run(
            ["uv", "run", args.cmd, tool, *tool_argv],
            cwd=cli_dir,
            timeout=180,
        )
        if proc.returncode != 0:
            _fail(tool, (proc.stderr or proc.stdout or "")[:800])
            return 1
        snippet = ((proc.stdout or "") + (proc.stderr or "")).strip().replace("\n", " ")[:160]
        _ok(tool, snippet or "exit 0")

    print()
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
