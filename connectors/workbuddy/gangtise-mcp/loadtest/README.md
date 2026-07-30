# Gangtise MCP 压测

对 WorkBuddy Connector（streamableHttp）生产端点做基准压测，覆盖：

1. **基础混合调用** — 额定 QPS 对 `health` / `initialize` / `tools/list` / 多域 Tool 混合调用  
2. **突发流量** — 2× 额定 QPS 持续 30s  
3. **长连接保持** — 200+ keep-alive 连接持续 10 分钟  

## 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

可选：本机安装 [k6](https://k6.io/)（报告中记录版本；协议压测以本脚本为准）。

## 运行

```bash
export MCP_BASE_URL=https://openapi.gangtise.com/application/open-mcp/
export MCP_ACCESS_KEY=your_ak
export MCP_SECRET_KEY=your_sk

# 完整基准（约 10 分钟）
python run_loadtest.py --out-dir ./out

# 调试用短跑
python run_loadtest.py --quick --out-dir ./out-quick
```

产物：

| 文件 | 说明 |
|------|------|
| `out/LOADTEST_REPORT.md` | 压测报告（含达标表与图） |
| `out/qps.png` 等 | QPS / 延迟 / 错误率 / 资源曲线 |
| `out/summary.json` | 机器可读汇总 |
| `out/metrics.jsonl` | 逐请求样本 |

**勿将 AK/SK 写入仓库或报告正文。**

## 基准（WorkBuddy 2.2.5）

| 指标 | 要求 |
|------|------|
| QPS | ≥ 50 |
| P50 | ≤ 500ms |
| P99 | ≤ 3000ms |
| 超时率 | < 1%（>30s） |
| 错误率 | ≤ 0.5%（5xx+超时） |
| 并发长连接 | ≥ 200 |
| 持续时长 | ≥ 10 分钟 |
