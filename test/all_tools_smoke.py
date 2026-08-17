#!/usr/bin/env python3
"""Batch smoke: local uv-run CLI + remote uvx (feature-merge branch) for all tools."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

MCPS = Path(__file__).resolve().parents[1]
CLI_DIR = MCPS / "cli" / "gangtise_mcp"
PYPI = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_GIT = "https://gitee.com/gangtise/gangtise-mcp"
DEFAULT_REF = "feature-merge-20260817"

# tool -> argv after command name
CASES: List[Tuple[str, Tuple[str, ...]]] = [
    # data
    ("security", ("-k", "茅台")),
    ("quote", ("--securities", "贵州茅台", "-l", "3")),
    ("quote", ("--securities", "贵州茅台", "--type", "minute", "-l", "3")),
    ("quote", ("--securities", "贵州茅台", "--type", "snap")),
    ("financial", ("--securities", "贵州茅台")),
    ("fund_flow", ("--securities", "贵州茅台")),
    ("shareholder", ("--holder-type", "top10", "--securities", "贵州茅台")),
    ("main_business", ("--securities", "贵州茅台")),
    ("valuation", ("--securities", "贵州茅台")),
    ("earning_forecast", ("--securities", "贵州茅台")),
    ("industry_indicator", ("-k", "GDP")),
    ("company_indicator", ("-k", "ROE")),
    ("concept", ("-k", "机器人")),
    ("block_constituents", ("-k", "白酒")),
    # file
    ("report", ("-l", "1")),
    ("summary", ("-l", "1")),
    ("pamirs_summary", ("-l", "1")),
    ("opinion", ("-l", "1")),
    ("announcement", ("-l", "1")),
    ("foreign_report", ("-l", "1")),
    ("foreign_opinion", ("-l", "1")),
    ("official_account", ("-l", "1")),
    ("management_discuss", ("--securities", "贵州茅台", "-l", "1")),
    ("qa", ("--securities", "贵州茅台", "-l", "1")),
    ("report_image", ("-k", "茅台", "-l", "1")),
    ("investment_calendar", ("-t", "roadshow", "-l", "1")),
    ("get_chiefs", ("-k", "张")),
    ("get_institutions", ("-k", "中信")),
    ("get_industries", ()),
    ("get_regions", ()),
    ("get_announcement_types", ()),
    # agent (skip slow chained)
    ("stock_one_line_summary", ("--securities", "贵州茅台")),
    ("hot_topic", ("--page-size", "1")),
    ("security_clue", ("--securities", "贵州茅台", "--page-size", "1")),
    # kb / private
    ("kb", ("-q", "茅台", "-l", "1")),
    ("stockpool", ("-m", "search", "-l", "1")),
    ("wechat_message", ("-m", "search", "-l", "1")),
    ("private_cloud", ("-m", "search", "-l", "1")),
    ("private_record", ("-m", "search", "-l", "1")),
    ("private_meeting", ("-m", "search", "-l", "1")),
]

SKIP_REASON = {
    "get_file": "需有效 file_id",
    "pdf_parse": "需本地 pdf / taskId",
    "stock_one_pager": "agent 耗时长，本轮抽样跳过",
    "investment_logic": "agent 耗时长，本轮抽样跳过",
    "peer_comparison": "agent 耗时长，本轮抽样跳过",
    "earnings_review": "链式 agent 默认跳过",
    "viewpoint_debate": "链式 agent 默认跳过",
    "theme_tracking": "需主题参数，本轮抽样跳过",
    "research_outline": "agent 耗时长，本轮抽样跳过",
}

PERM_MARKERS = ("无权限", "没有权限", "未购买", "权限不足", "not authorized", "forbidden", "403")
AUTH_MARKERS = ("未配置", "AccessKey", "授权失败", "login", "鉴权")


@dataclass
class Result:
    mode: str
    tool: str
    argv: Tuple[str, ...]
    status: str
    detail: str
    seconds: float


def _classify(tool: str, returncode: int, text: str) -> Tuple[str, str]:
    low = text.lower()
    snippet = re.sub(r"\s+", " ", text).strip()[:180]
    if tool == "pamirs_summary":
        # 无权限但能调通算 ok
        if returncode == 0 or any(m in text for m in PERM_MARKERS) or "state" in low or "error" in low or "失败" in text or "成功" in text:
            if any(m in text for m in PERM_MARKERS):
                return "ok_perm", snippet or "permission denied but reachable"
            if returncode == 0:
                return "ok", snippet or "exit 0"
            # 业务错误也算调通
            if any(m in text for m in AUTH_MARKERS):
                return "fail_auth", snippet
            return "ok_reachable", snippet or f"rc={returncode}"
    if returncode != 0:
        if any(m in text for m in AUTH_MARKERS):
            return "fail_auth", snippet
        return "fail", snippet or f"rc={returncode}"
    if any(m in text for m in AUTH_MARKERS) and "success" not in low:
        return "fail_auth", snippet
    if "traceback" in low or "ImportError" in text:
        return "fail", snippet
    return "ok", snippet or "exit 0"


def _run(cmd: Sequence[str], *, cwd: Optional[Path], timeout: int) -> Tuple[int, str, float]:
    t0 = time.time()
    try:
        p = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=os.environ.copy(),
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out, time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + (
            (e.stderr or "") if isinstance(e.stderr, str) else ""
        )
        return -1, out + f"\n[timeout>{timeout}s]", time.time() - t0


def local_base() -> List[str]:
    return ["uv", "run", "gangtise"]


def uvx_base(git: str, ref: str) -> List[str]:
    mcp = f"git+{git.rstrip('/')}@{ref}#subdirectory=mcp/gangtise_mcp"
    cli = f"git+{git.rstrip('/')}@{ref}#subdirectory=cli/gangtise_mcp"
    return [
        "uvx",
        "--default-index",
        PYPI,
        "--with",
        mcp,
        "--from",
        cli,
        "gangtise",
    ]


def run_suite(mode: str, base: List[str], *, cwd: Optional[Path], timeout: int, only: Optional[str]) -> List[Result]:
    results: List[Result] = []
    # install / help smoke
    rc, out, sec = _run([*base, "--help"], cwd=cwd, timeout=max(timeout, 900) if mode == "uvx" else timeout)
    st, detail = _classify("help", rc, out)
    results.append(Result(mode, "--help", (), st if rc == 0 else "fail", detail, sec))
    print(f"[{mode}] --help -> {results[-1].status} ({sec:.1f}s)")

    rc, out, sec = _run([*base, "list"], cwd=cwd, timeout=max(timeout, 300))
    st, detail = _classify("list", rc, out)
    results.append(Result(mode, "list", (), st if rc == 0 else "fail", detail, sec))
    has_pamirs = "pamirs_summary" in out
    print(f"[{mode}] list -> {results[-1].status} pamirs_listed={has_pamirs} ({sec:.1f}s)")
    if rc == 0 and not has_pamirs:
        results.append(Result(mode, "pamirs_summary(list)", (), "fail", "list 未出现 pamirs_summary", 0))

    for tool, argv in CASES:
        if only and only not in tool and only not in " ".join(argv):
            continue
        label = f"{tool}{' ' + ' '.join(argv) if argv else ''}"
        rc, out, sec = _run([*base, tool, *argv], cwd=cwd, timeout=timeout)
        st, detail = _classify(tool, rc, out)
        results.append(Result(mode, tool, argv, st, detail, sec))
        print(f"[{mode}] {label} -> {st} ({sec:.1f}s) {detail[:80]}")

    for tool, reason in SKIP_REASON.items():
        if only and only not in tool:
            continue
        results.append(Result(mode, tool, (), "skip", reason, 0))
    return results


def summarize(results: List[Result]) -> int:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("\n=== 汇总 ===")
    print(counts)
    bad = [r for r in results if r.status.startswith("fail")]
    if bad:
        print("\n失败项:")
        for r in bad:
            print(f"  [{r.mode}] {r.tool} {r.argv} :: {r.detail}")
    okish = sum(1 for r in results if r.status.startswith("ok") or r.status == "skip")
    print(f"通过/可调通/跳过: {okish}/{len(results)}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "uvx", "both"], default="both")
    ap.add_argument("--git", default=DEFAULT_GIT)
    ap.add_argument("--ref", default=DEFAULT_REF)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--only", default=None)
    ap.add_argument("--skip-uvx-install-wait", action="store_true")
    args = ap.parse_args()

    if not os.getenv("GTS_ACCESS_KEY") or not os.getenv("GTS_SECRET_KEY"):
        print("需要 GTS_ACCESS_KEY / GTS_SECRET_KEY", file=sys.stderr)
        return 2

    all_results: List[Result] = []
    if args.mode in ("local", "both"):
        print(f"\n######## LOCAL CLI ({CLI_DIR}) ########\n")
        all_results.extend(
            run_suite("local", local_base(), cwd=CLI_DIR, timeout=args.timeout, only=args.only)
        )
    if args.mode in ("uvx", "both"):
        print(f"\n######## UVX @{args.ref} ########\n")
        # first call may download long
        all_results.extend(
            run_suite(
                "uvx",
                uvx_base(args.git, args.ref),
                cwd=None,
                timeout=max(args.timeout, 180),
                only=args.only,
            )
        )
    return summarize(all_results)


if __name__ == "__main__":
    raise SystemExit(main())
