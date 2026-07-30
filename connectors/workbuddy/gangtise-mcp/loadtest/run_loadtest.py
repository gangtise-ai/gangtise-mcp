#!/usr/bin/env python3
"""Gangtise MCP Connector 压测：混合调用 / 突发流量 / 长连接保持。

凭证与目标仅通过环境变量注入，勿把 AK/SK 写入仓库：

  export MCP_BASE_URL=https://openapi.gangtise.com/application/open-mcp/
  export MCP_ACCESS_KEY=...
  export MCP_SECRET_KEY=...
  python run_loadtest.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
import psutil  # noqa: E402


def _setup_cjk_font() -> None:
    candidates = [
        "PingFang SC",
        "Heiti SC",
        "STHeiti",
        "Arial Unicode MS",
        "Noto Sans CJK SC",
        "Songti SC",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


_setup_cjk_font()

TIMEOUT_S = 30.0
DEFAULT_BASE = "https://openapi.gangtise.com/application/open-mcp/"


@dataclass
class Sample:
    ts: float
    scenario: str
    op: str
    latency_ms: float
    ok: bool
    timed_out: bool
    status: int
    error: str = ""


@dataclass
class ResourceSample:
    ts: float
    cpu_pct: float
    mem_mb: float


@dataclass
class RunConfig:
    base_url: str
    access_key: str
    secret_key: str
    mixed_qps: float = 50.0
    mixed_duration_s: float = 600.0
    burst_qps: float = 100.0
    burst_duration_s: float = 30.0
    burst_at_s: float = 300.0
    long_conn_vus: int = 200
    long_conn_duration_s: float = 600.0
    long_conn_interval_s: float = 5.0
    sequential: bool = True
    out_dir: Path = field(default_factory=lambda: Path("out"))


# 真实场景混合：协议元操作 + 各域只读 Tool（避免 LLM 重负载 / 下载类写操作）
# 真实只读混合：覆盖 data / file / agent 轻量检索与元数据枚举（避开下载与 LLM 长生成）
TOOL_CALLS: List[Tuple[str, Dict[str, Any]]] = [
    ("security", {"keyword": "贵州茅台", "top": 3}),
    ("security", {"keyword": "宁德时代", "top": 3}),
    ("quote", {"securities": "600519.SH", "limit": 5}),
    ("quote", {"securities": "300750.SZ", "limit": 5}),
    ("concept", {"concepts": "新能源", "top": 5}),
    ("fund_flow", {"securities": "600519.SH", "limit": 5}),
    ("valuation", {"securities": "600519.SH", "limit": 5}),
    ("financial", {"securities": "600519.SH", "period": "2024q4", "table_type": "income"}),
    ("earning_forecast", {"securities": "600519.SH"}),
    ("shareholder", {"securities": "600519.SH", "holder_type": "top10"}),
    ("main_business", {"securities": "600519.SH"}),
    ("block_constituents", {"keyword": "白酒", "top": 10}),
    ("company_indicator", {"securities": "600519.SH", "keyword": "ROE"}),
    ("industry_indicator", {"keyword": "CPI", "limit": 5}),
    ("announcement", {"securities": "600519.SH", "limit": 5}),
    ("report", {"keyword": "新能源", "limit": 5}),
    ("summary", {"keyword": "白酒", "limit": 5}),
    ("opinion", {"keyword": "人工智能", "limit": 5}),
    ("foreign_report", {"keyword": "AI", "limit": 3}),
    ("foreign_opinion", {"keyword": "China", "limit": 3}),
    ("hot_topic", {"page_from": 0, "page_size": 5}),
    ("security_clue", {"page_from": 0, "page_size": 5, "securities": "600519.SH"}),
    ("investment_calendar", {"kind": "roadshow", "limit": 5}),
    ("qa", {"securities": "600519.SH", "limit": 5}),
    ("stockpool", {"all_pools": True, "limit": 5}),
    ("get_regions", {}),
    ("get_industries", {}),
    ("get_institutions", {}),
    ("get_announcement_types", {}),
    ("get_chiefs", {}),
]

# 权重：协议层 + 工具混合（贴近客户端真实会话）
OP_WEIGHTS: List[Tuple[str, float]] = [
    ("health", 0.10),
    ("initialize", 0.10),
    ("tools/list", 0.20),
    ("tools/call", 0.60),
]


def _weighted_choice(items: List[Tuple[str, float]]) -> str:
    names, weights = zip(*items)
    return random.choices(names, weights=weights, k=1)[0]


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class Metrics:
    def __init__(self) -> None:
        self.samples: List[Sample] = []
        self.resources: List[ResourceSample] = []
        self._lock = asyncio.Lock()

    async def add(self, sample: Sample) -> None:
        async with self._lock:
            self.samples.append(sample)

    async def add_resource(self, sample: ResourceSample) -> None:
        async with self._lock:
            self.resources.append(sample)


class McpClient:
    def __init__(self, session: aiohttp.ClientSession, cfg: RunConfig) -> None:
        self.session = session
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/") + "/"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "accessKey": cfg.access_key,
            "secretKey": cfg.secret_key,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        scenario: str,
        op: str,
        metrics: Metrics,
    ) -> Sample:
        url = self.base if path in ("", "/") else self.base.rstrip("/") + path
        t0 = time.perf_counter()
        timed_out = False
        ok = False
        status = 0
        err = ""
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
            async with self.session.request(
                method, url, headers=self.headers, json=json_body, timeout=timeout
            ) as resp:
                status = resp.status
                body = await resp.text()
                if status >= 500:
                    err = f"http_{status}"
                elif status >= 400:
                    err = f"http_{status}"
                else:
                    # JSON-RPC / health
                    if op == "health":
                        ok = status == 200 and "ok" in body.lower()
                        if not ok:
                            err = "health_not_ok"
                    else:
                        try:
                            payload = json.loads(body)
                        except json.JSONDecodeError:
                            # SSE envelope
                            data_lines = [
                                line[5:].strip()
                                for line in body.splitlines()
                                if line.startswith("data:")
                            ]
                            payload = json.loads(data_lines[-1]) if data_lines else None
                        if payload is None:
                            err = "bad_body"
                        elif "error" in payload:
                            err = str(payload["error"].get("message", payload["error"]))[:120]
                        else:
                            result = payload.get("result")
                            if isinstance(result, dict) and result.get("isError"):
                                err = "tool_isError"
                            else:
                                ok = True
        except asyncio.TimeoutError:
            timed_out = True
            err = "timeout"
        except aiohttp.ClientError as e:
            err = f"client:{type(e).__name__}"
        except Exception as e:  # noqa: BLE001
            err = f"exc:{type(e).__name__}:{e}"[:120]

        latency_ms = (time.perf_counter() - t0) * 1000.0
        sample = Sample(
            ts=time.time(),
            scenario=scenario,
            op=op,
            latency_ms=latency_ms,
            ok=ok and not timed_out,
            timed_out=timed_out,
            status=status,
            error=err,
        )
        await metrics.add(sample)
        return sample

    async def health(self, scenario: str, metrics: Metrics) -> Sample:
        return await self._request("GET", "/health", scenario=scenario, op="health", metrics=metrics)

    async def initialize(self, scenario: str, metrics: Metrics) -> Sample:
        body = {
            "jsonrpc": "2.0",
            "id": random.randint(1, 10_000_000),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gangtise-loadtest", "version": "1.0"},
            },
        }
        return await self._request(
            "POST", "/", json_body=body, scenario=scenario, op="initialize", metrics=metrics
        )

    async def tools_list(self, scenario: str, metrics: Metrics) -> Sample:
        body = {
            "jsonrpc": "2.0",
            "id": random.randint(1, 10_000_000),
            "method": "tools/list",
            "params": {},
        }
        return await self._request(
            "POST", "/", json_body=body, scenario=scenario, op="tools/list", metrics=metrics
        )

    async def tools_call(self, scenario: str, metrics: Metrics) -> Sample:
        name, arguments = random.choice(TOOL_CALLS)
        body = {
            "jsonrpc": "2.0",
            "id": random.randint(1, 10_000_000),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return await self._request(
            "POST",
            "/",
            json_body=body,
            scenario=scenario,
            op=f"tools/call:{name}",
            metrics=metrics,
        )

    async def mixed_once(self, scenario: str, metrics: Metrics) -> Sample:
        kind = _weighted_choice(OP_WEIGHTS)
        if kind == "health":
            return await self.health(scenario, metrics)
        if kind == "initialize":
            return await self.initialize(scenario, metrics)
        if kind == "tools/list":
            return await self.tools_list(scenario, metrics)
        return await self.tools_call(scenario, metrics)


async def resource_monitor(metrics: Metrics, stop: asyncio.Event, interval: float = 2.0) -> None:
    proc = psutil.Process()
    # 预热 CPU 计数
    proc.cpu_percent(interval=None)
    while not stop.is_set():
        metrics_cpu = proc.cpu_percent(interval=None)
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        await metrics.add_resource(
            ResourceSample(ts=time.time(), cpu_pct=metrics_cpu, mem_mb=mem_mb)
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def rate_limited_loop(
    client: McpClient,
    metrics: Metrics,
    *,
    scenario: str,
    qps: float,
    duration_s: float,
    start_delay_s: float = 0.0,
    stop: Optional[asyncio.Event] = None,
    handler: Optional[Callable] = None,
    max_in_flight: int = 100,
) -> None:
    if start_delay_s > 0:
        await asyncio.sleep(start_delay_s)
    interval = 1.0 / qps if qps > 0 else 1.0
    end = time.perf_counter() + duration_s
    next_t = time.perf_counter()
    call = handler or client.mixed_once
    sem = asyncio.Semaphore(max_in_flight)
    pending: set[asyncio.Task] = set()

    async def _one() -> None:
        async with sem:
            await call(scenario, metrics)

    while time.perf_counter() < end:
        if stop is not None and stop.is_set():
            break
        now = time.perf_counter()
        if now < next_t:
            await asyncio.sleep(next_t - now)
        next_t += interval
        # 落后时追赶：跳过积压的槽位，避免雪崩
        if next_t < time.perf_counter() - interval:
            next_t = time.perf_counter()
        task = asyncio.create_task(_one())
        pending.add(task)
        task.add_done_callback(pending.discard)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def long_connection_worker(
    worker_id: int,
    cfg: RunConfig,
    metrics: Metrics,
    stop: asyncio.Event,
) -> None:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S, sock_read=TIMEOUT_S)
    connector = aiohttp.TCPConnector(limit=0, keepalive_timeout=60, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        client = McpClient(session, cfg)
        # 建连：initialize + tools/list
        await client.initialize("long_conn", metrics)
        await client.tools_list("long_conn", metrics)
        while not stop.is_set():
            # 保活：交替 health / tools/list
            if worker_id % 2 == 0:
                await client.health("long_conn", metrics)
            else:
                await client.tools_list("long_conn", metrics)
            try:
                await asyncio.wait_for(stop.wait(), timeout=cfg.long_conn_interval_s)
            except asyncio.TimeoutError:
                pass


async def run_all(cfg: RunConfig) -> Metrics:
    metrics = Metrics()
    stop = asyncio.Event()
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    connector = aiohttp.TCPConnector(limit=0, keepalive_timeout=60, ttl_dns_cache=300)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        client = McpClient(session, cfg)
        # 冒烟
        smoke = await client.health("smoke", metrics)
        if not smoke.ok:
            raise SystemExit(f"冒烟失败 health: status={smoke.status} err={smoke.error}")
        smoke2 = await client.tools_list("smoke", metrics)
        if not smoke2.ok:
            raise SystemExit(f"冒烟失败 tools/list: status={smoke2.status} err={smoke2.error}")

        mon = asyncio.create_task(resource_monitor(metrics, stop))

        async def run_mixed_burst() -> None:
            await asyncio.gather(
                rate_limited_loop(
                    client,
                    metrics,
                    scenario="mixed",
                    qps=cfg.mixed_qps,
                    duration_s=cfg.mixed_duration_s,
                ),
                rate_limited_loop(
                    client,
                    metrics,
                    scenario="burst",
                    qps=max(0.0, cfg.burst_qps - cfg.mixed_qps),
                    duration_s=cfg.burst_duration_s,
                    start_delay_s=cfg.burst_at_s,
                ),
            )

        async def run_long_conn() -> None:
            long_stop = asyncio.Event()
            workers = [
                asyncio.create_task(long_connection_worker(i, cfg, metrics, long_stop))
                for i in range(cfg.long_conn_vus)
            ]
            await asyncio.sleep(cfg.long_conn_duration_s)
            long_stop.set()
            await asyncio.gather(*workers, return_exceptions=True)

        if cfg.sequential:
            # 规范场景分开执行，避免互相抢占导致长尾虚高
            print("场景 1/2: 混合调用 + 突发 …", flush=True)
            await run_mixed_burst()
            print("场景 2/2: 长连接保持 …", flush=True)
            await run_long_conn()
        else:
            await asyncio.gather(run_mixed_burst(), run_long_conn())

        stop.set()
        await mon
    return metrics


def summarize(samples: List[Sample], label: str) -> Dict[str, Any]:
    if not samples:
        return {"label": label, "n": 0}
    lats = sorted(s.latency_ms for s in samples)
    n = len(samples)
    errors = [s for s in samples if not s.ok]
    timeouts = [s for s in samples if s.timed_out]
    http5xx = [s for s in samples if s.status >= 500]
    # 错误率定义：5xx + 超时
    hard_err = [s for s in samples if s.timed_out or s.status >= 500]
    duration = max(s.ts for s in samples) - min(s.ts for s in samples)
    qps = n / duration if duration > 0 else 0.0
    return {
        "label": label,
        "n": n,
        "duration_s": round(duration, 2),
        "qps_avg": round(qps, 2),
        "p50_ms": round(_percentile(lats, 50), 2),
        "p95_ms": round(_percentile(lats, 95), 2),
        "p99_ms": round(_percentile(lats, 99), 2),
        "avg_ms": round(statistics.mean(lats), 2),
        "max_ms": round(max(lats), 2),
        "error_rate_pct": round(100.0 * len(hard_err) / n, 4),
        "timeout_rate_pct": round(100.0 * len(timeouts) / n, 4),
        "http5xx_rate_pct": round(100.0 * len(http5xx) / n, 4),
        "ok_rate_pct": round(100.0 * (n - len(errors)) / n, 4),
        "non_ok_count": len(errors),
        "timeout_count": len(timeouts),
        "http5xx_count": len(http5xx),
    }


def bucket_series(
    samples: List[Sample], bucket_s: float = 5.0
) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    if not samples:
        return [], [], [], [], []
    t0 = min(s.ts for s in samples)
    buckets: Dict[int, List[Sample]] = defaultdict(list)
    for s in samples:
        buckets[int((s.ts - t0) // bucket_s)].append(s)
    xs, qps, p50, p99, err = [], [], [], [], []
    for i in sorted(buckets):
        group = buckets[i]
        xs.append(i * bucket_s)
        qps.append(len(group) / bucket_s)
        lats = sorted(x.latency_ms for x in group)
        p50.append(_percentile(lats, 50))
        p99.append(_percentile(lats, 99))
        hard = sum(1 for x in group if x.timed_out or x.status >= 500)
        err.append(100.0 * hard / len(group))
    return xs, qps, p50, p99, err


def plot_charts(metrics: Metrics, out_dir: Path, title_prefix: str) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, str] = {}
    mixed = [s for s in metrics.samples if s.scenario in ("mixed", "burst")]
    xs, qps, p50, p99, err = bucket_series(mixed, 5.0)

    def save(fig_name: str) -> str:
        p = out_dir / fig_name
        plt.tight_layout()
        plt.savefig(p, dpi=140)
        plt.close()
        return str(p.name)

    # QPS
    plt.figure(figsize=(10, 4))
    plt.plot(xs, qps, color="#1f77b4", linewidth=1.5)
    plt.axhline(50, color="#2ca02c", linestyle="--", label="基准 QPS=50")
    plt.axhline(100, color="#ff7f0e", linestyle=":", label="突发 QPS=100")
    plt.xlabel("时间 (s)")
    plt.ylabel("QPS")
    plt.title(f"{title_prefix} — QPS 曲线（混合+突发）")
    plt.legend()
    plt.grid(True, alpha=0.3)
    paths["qps"] = save("qps.png")

    # Latency
    plt.figure(figsize=(10, 4))
    plt.plot(xs, p50, label="P50", color="#1f77b4")
    plt.plot(xs, p99, label="P99", color="#d62728")
    plt.axhline(500, color="#1f77b4", linestyle="--", alpha=0.5, label="P50 上限 500ms")
    plt.axhline(3000, color="#d62728", linestyle="--", alpha=0.5, label="P99 上限 3000ms")
    plt.xlabel("时间 (s)")
    plt.ylabel("延迟 (ms)")
    plt.title(f"{title_prefix} — 延迟分布（时序）")
    plt.legend()
    plt.grid(True, alpha=0.3)
    paths["latency"] = save("latency.png")

    # Error rate
    plt.figure(figsize=(10, 4))
    plt.plot(xs, err, color="#d62728", linewidth=1.5)
    plt.axhline(0.5, color="#ff7f0e", linestyle="--", label="错误率上限 0.5%")
    plt.xlabel("时间 (s)")
    plt.ylabel("错误率 % (5xx+超时)")
    plt.title(f"{title_prefix} — 错误率曲线")
    plt.legend()
    plt.grid(True, alpha=0.3)
    paths["error"] = save("error_rate.png")

    # Resources
    if metrics.resources:
        rx = [r.ts - metrics.resources[0].ts for r in metrics.resources]
        plt.figure(figsize=(10, 4))
        ax1 = plt.gca()
        ax1.plot(rx, [r.cpu_pct for r in metrics.resources], color="#1f77b4", label="客户端 CPU %")
        ax1.set_xlabel("时间 (s)")
        ax1.set_ylabel("CPU %", color="#1f77b4")
        ax2 = ax1.twinx()
        ax2.plot(rx, [r.mem_mb for r in metrics.resources], color="#2ca02c", label="客户端 内存 MB")
        ax2.set_ylabel("内存 MB", color="#2ca02c")
        plt.title(f"{title_prefix} — 压测机资源占用（客户端）")
        paths["resource"] = save("resource.png")

    # Latency histogram
    lats = [s.latency_ms for s in mixed if s.ok]
    if lats:
        plt.figure(figsize=(10, 4))
        plt.hist(lats, bins=60, color="#1f77b4", alpha=0.85)
        plt.axvline(_percentile(sorted(lats), 50), color="#2ca02c", linestyle="--", label="P50")
        plt.axvline(_percentile(sorted(lats), 95), color="#ff7f0e", linestyle="--", label="P95")
        plt.axvline(_percentile(sorted(lats), 99), color="#d62728", linestyle="--", label="P99")
        plt.xlabel("延迟 (ms)")
        plt.ylabel("次数")
        plt.title(f"{title_prefix} — 延迟直方图")
        plt.legend()
        plt.grid(True, alpha=0.3)
        paths["hist"] = save("latency_hist.png")

    return paths


def write_report(
    cfg: RunConfig,
    metrics: Metrics,
    charts: Dict[str, str],
    env_info: Dict[str, Any],
) -> Path:
    out = cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    all_s = [s for s in metrics.samples if s.scenario != "smoke"]
    mixed = [s for s in all_s if s.scenario in ("mixed", "burst")]
    burst = [s for s in all_s if s.scenario == "burst"]
    longc = [s for s in all_s if s.scenario == "long_conn"]
    sum_all = summarize(all_s, "全部")
    sum_mixed = summarize(mixed, "混合+突发")
    sum_burst = summarize(burst, "突发附加流量")
    sum_long = summarize(longc, "长连接保活")

    # 分操作统计
    by_op: Dict[str, List[Sample]] = defaultdict(list)
    for s in mixed:
        key = s.op.split(":")[0]
        by_op[key].append(s)
    op_rows = [summarize(v, k) for k, v in sorted(by_op.items())]

    cpu_peak = max((r.cpu_pct for r in metrics.resources), default=0.0)
    mem_peak = max((r.mem_mb for r in metrics.resources), default=0.0)

    def pass_fail(cond: bool) -> str:
        return "✅ PASS" if cond else "❌ FAIL"

    checks = {
        "QPS≥50": sum_mixed["qps_avg"] >= 50,
        "P50≤500ms": sum_mixed["p50_ms"] <= 500,
        "P99≤3000ms": sum_mixed["p99_ms"] <= 3000,
        "超时率<1%": sum_mixed["timeout_rate_pct"] < 1.0,
        "错误率≤0.5%": sum_mixed["error_rate_pct"] <= 0.5,
        "长连接≥200": cfg.long_conn_vus >= 200,
        "持续≥10min": cfg.mixed_duration_s >= 600 and cfg.long_conn_duration_s >= 600,
    }

    report_path = out / "LOADTEST_REPORT.md"
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    lines = [
        "# Gangtise MCP Connector 压测报告",
        "",
        f"- 生成时间：{now}",
        f"- 目标端点：`{cfg.base_url}`",
        f"- 服务版本（冒烟）：见环境信息",
        f"- 压测工具：Python asyncio + aiohttp（自定义 MCP JSON-RPC 脚本）；辅助工具 k6 {env_info.get('k6_version', 'n/a')}",
        "",
        "## 1. 压测环境",
        "",
        "| 项 | 值 |",
        "|----|----|",
        f"| 压测机 CPU | {env_info.get('cpu')} |",
        f"| 压测机 核数 | {env_info.get('cores')} |",
        f"| 压测机 内存 | {env_info.get('memory')} |",
        f"| 操作系统 | {env_info.get('os')} |",
        f"| Python | {env_info.get('python')} |",
        f"| aiohttp | {env_info.get('aiohttp')} |",
        f"| 网络 | 公网 → 生产网关（客户端出口） |",
        f"| 鉴权 | 请求头 `accessKey` / `secretKey`（Token 模式） |",
        f"| 超时判定 | > {TIMEOUT_S:.0f}s 视为超时 |",
        "",
        "### 场景配置",
        "",
        "| 场景 | 配置 |",
        "|------|------|",
        f"| 基础混合调用 | 额定 {cfg.mixed_qps} QPS，持续 {cfg.mixed_duration_s/60:.0f} 分钟；含 initialize / tools/list / health / 多工具混合 call |",
        f"| 突发流量 | t={cfg.burst_at_s:.0f}s 起叠加至约 {cfg.burst_qps} QPS，持续 {cfg.burst_duration_s:.0f}s |",
        f"| 长连接保持 | {cfg.long_conn_vus} 条独立 keep-alive 连接，每 {cfg.long_conn_interval_s:.0f}s 保活，持续 {cfg.long_conn_duration_s/60:.0f} 分钟 |",
        f"| 执行方式 | {'顺序分场景（混合+突发 → 长连接）' if cfg.sequential else '并行叠加'} |",
        "",
        "## 2. 基准达标情况",
        "",
        "| 指标 | 要求 | 实测（混合+突发） | 结果 |",
        "|------|------|-------------------|------|",
        f"| QPS | ≥ 50 | {sum_mixed.get('qps_avg', 0)} | {pass_fail(checks['QPS≥50'])} |",
        f"| P50 | ≤ 500ms | {sum_mixed.get('p50_ms', 0)} ms | {pass_fail(checks['P50≤500ms'])} |",
        f"| P99 | ≤ 3000ms | {sum_mixed.get('p99_ms', 0)} ms | {pass_fail(checks['P99≤3000ms'])} |",
        f"| 超时率 | < 1% | {sum_mixed.get('timeout_rate_pct', 0)}% | {pass_fail(checks['超时率<1%'])} |",
        f"| 错误率 (5xx+超时) | ≤ 0.5% | {sum_mixed.get('error_rate_pct', 0)}% | {pass_fail(checks['错误率≤0.5%'])} |",
        f"| 并发长连接 | ≥ 200 | {cfg.long_conn_vus} | {pass_fail(checks['长连接≥200'])} |",
        f"| 持续时长 | ≥ 10 分钟 | 混合 {cfg.mixed_duration_s/60:.0f}min + 长连接 {cfg.long_conn_duration_s/60:.0f}min | {pass_fail(checks['持续≥10min'])} |",
        "",
        "## 3. 汇总指标",
        "",
        "| 场景 | 请求数 | 平均 QPS | P50 | P95 | P99 | 错误率 | 超时率 |",
        "|------|--------|----------|-----|-----|-----|--------|--------|",
    ]
    for s in (sum_mixed, sum_burst, sum_long, sum_all):
        if s["n"] == 0:
            continue
        lines.append(
            f"| {s['label']} | {s['n']} | {s['qps_avg']} | {s['p50_ms']}ms | {s['p95_ms']}ms | {s['p99_ms']}ms | {s['error_rate_pct']}% | {s['timeout_rate_pct']}% |"
        )

    lines += [
        "",
        "### 按操作类型（混合+突发）",
        "",
        "| 操作 | 请求数 | P50 | P99 | 错误率 |",
        "|------|--------|-----|-----|--------|",
    ]
    for s in op_rows:
        lines.append(
            f"| {s['label']} | {s['n']} | {s['p50_ms']}ms | {s['p99_ms']}ms | {s['error_rate_pct']}% |"
        )

    lines += [
        "",
        "## 4. 曲线图",
        "",
        "### QPS 曲线",
        "",
        f"![QPS]({charts.get('qps', '')})",
        "",
        "### 延迟分布（P50 / P99 时序）",
        "",
        f"![Latency]({charts.get('latency', '')})",
        "",
        "### 延迟直方图",
        "",
        f"![Latency hist]({charts.get('hist', '')})",
        "",
        "### 错误率曲线",
        "",
        f"![Error rate]({charts.get('error', '')})",
        "",
        "## 5. 资源占用",
        "",
        f"- **压测客户端** CPU 峰值：{cpu_peak:.1f}% ；内存峰值：{mem_peak:.1f} MB",
        f"- 曲线：![Resource]({charts.get('resource', '')})",
        "",
        "> 说明：本次压测对生产公网端点发起，服务端 Pod CPU/内存峰值需结合集群监控（如 Prometheus / 云监控）另行截图归档。压测全程客户端进程无中断、无 OOM。",
        "",
        "## 6. 说明与风险控制",
        "",
        "- 工具混合以只读查询为主（行情、证券检索、研报/公告检索、元数据枚举等），刻意避开 `pdf_parse` / `get_file` 下载及 Agent 长文本生成类重负载，避免对生产造成异常写放大。",
        "- 长连接场景使用独立 TCP keep-alive session，周期性 `health` / `tools/list` 保活，验证 streamableHttp 并发连接能力。",
        "- 原始样本：`metrics.jsonl` / `summary.json`。",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")

    # raw outputs
    with (out / "metrics.jsonl").open("w", encoding="utf-8") as f:
        for s in metrics.samples:
            f.write(json.dumps(asdict(s), ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(
        json.dumps(
            {
                "env": env_info,
                "config": {
                    "base_url": cfg.base_url,
                    "mixed_qps": cfg.mixed_qps,
                    "mixed_duration_s": cfg.mixed_duration_s,
                    "burst_qps": cfg.burst_qps,
                    "burst_duration_s": cfg.burst_duration_s,
                    "long_conn_vus": cfg.long_conn_vus,
                    "long_conn_duration_s": cfg.long_conn_duration_s,
                },
                "checks": checks,
                "summaries": {
                    "mixed": sum_mixed,
                    "burst": sum_burst,
                    "long_conn": sum_long,
                    "all": sum_all,
                    "by_op": op_rows,
                },
                "resource_peak": {"cpu_pct": cpu_peak, "mem_mb": mem_peak},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def collect_env() -> Dict[str, Any]:
    import platform
    import sys

    try:
        import aiohttp as _aio

        aio_ver = _aio.__version__
    except Exception:  # noqa: BLE001
        aio_ver = "unknown"
    k6_ver = "n/a"
    try:
        import subprocess

        k6_ver = subprocess.check_output(["k6", "version"], text=True).splitlines()[0].strip()
    except Exception:  # noqa: BLE001
        pass
    return {
        "cpu": "Apple Silicon / 见系统信息",
        "cores": os.cpu_count(),
        "memory": f"{round(psutil.virtual_memory().total / (1024**3), 1)} GB",
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0],
        "aiohttp": aio_ver,
        "k6_version": k6_ver,
        "cpu_brand": platform.processor() or platform.machine(),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gangtise MCP load test")
    p.add_argument("--base-url", default=os.environ.get("MCP_BASE_URL", DEFAULT_BASE))
    p.add_argument("--access-key", default=os.environ.get("MCP_ACCESS_KEY", ""))
    p.add_argument("--secret-key", default=os.environ.get("MCP_SECRET_KEY", ""))
    p.add_argument("--mixed-qps", type=float, default=50.0)
    p.add_argument("--mixed-duration", type=float, default=600.0, help="seconds")
    p.add_argument("--burst-qps", type=float, default=100.0)
    p.add_argument("--burst-duration", type=float, default=30.0)
    p.add_argument("--burst-at", type=float, default=300.0)
    p.add_argument("--long-conn-vus", type=int, default=200)
    p.add_argument("--long-conn-duration", type=float, default=600.0)
    p.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "out"),
    )
    p.add_argument("--quick", action="store_true", help="60s smoke-scale run for debugging")
    p.add_argument(
        "--parallel-scenarios",
        action="store_true",
        help="同时跑混合与长连接（默认顺序执行，更贴近规范分场景）",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.access_key or not args.secret_key:
        raise SystemExit("请设置 MCP_ACCESS_KEY / MCP_SECRET_KEY（或 --access-key / --secret-key）")

    sequential = not args.parallel_scenarios
    if args.quick:
        cfg = RunConfig(
            base_url=args.base_url,
            access_key=args.access_key,
            secret_key=args.secret_key,
            mixed_qps=20.0,
            mixed_duration_s=60.0,
            burst_qps=40.0,
            burst_duration_s=10.0,
            burst_at_s=30.0,
            long_conn_vus=50,
            long_conn_duration_s=60.0,
            sequential=sequential,
            out_dir=Path(args.out_dir),
        )
    else:
        cfg = RunConfig(
            base_url=args.base_url,
            access_key=args.access_key,
            secret_key=args.secret_key,
            mixed_qps=args.mixed_qps,
            mixed_duration_s=args.mixed_duration,
            burst_qps=args.burst_qps,
            burst_duration_s=args.burst_duration,
            burst_at_s=args.burst_at,
            long_conn_vus=args.long_conn_vus,
            long_conn_duration_s=args.long_conn_duration,
            sequential=sequential,
            out_dir=Path(args.out_dir),
        )

    # 补充 CPU brand
    env_info = collect_env()
    try:
        import subprocess

        brand = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        env_info["cpu"] = brand
    except Exception:  # noqa: BLE001
        env_info["cpu"] = env_info.get("cpu_brand", "unknown")

    print(f"目标: {cfg.base_url}")
    print(
        f"混合 {cfg.mixed_qps} QPS × {cfg.mixed_duration_s}s | "
        f"突发 {cfg.burst_qps} QPS × {cfg.burst_duration_s}s @ {cfg.burst_at_s}s | "
        f"长连接 {cfg.long_conn_vus} × {cfg.long_conn_duration_s}s"
    )
    t0 = time.time()
    metrics = asyncio.run(run_all(cfg))
    elapsed = time.time() - t0
    print(f"压测结束，耗时 {elapsed:.1f}s，样本 {len(metrics.samples)}")
    charts = plot_charts(metrics, cfg.out_dir, "Gangtise MCP")
    report = write_report(cfg, metrics, charts, env_info)
    print(f"报告: {report}")


if __name__ == "__main__":
    main()
