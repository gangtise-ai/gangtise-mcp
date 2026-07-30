<div align="center">

# Gangtise Data MCP

**简体中文** | [English](README.md)

行情、财务、估值、资金流向、行业/公司指标、题材指数等。

> **日常推荐**：请优先使用整合包 [`gangtise_mcp`](../gangtise_mcp/)（五域全部叶子工具）。本页仅介绍本单包。

[仓库总览](../../README.cn.md) · [开放平台](https://open-platform.gangtise.com/)

</div>

---

## 工具

`block_constituents`, `company_indicator`, `concept`, `earning_forecast`, `financial`, `fund_flow`, `industry_indicator`, `main_business`, `quote`, `security`, `shareholder`, `valuation`

---

<details>
<summary><b>本包安装（Cursor / WorkBuddy）</b></summary>

账号：[开放平台](https://open-platform.gangtise.com/)。需安装 [uv](https://docs.astral.sh/uv/)。

**Cursor** — 推荐将下方 JSON 发给 Cursor **Agent** 由其安装（改动出现时 Accept），再在 Settings → Tools & MCP 确认；亦可手动写入 `~/.cursor/mcp.json` 或项目 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "gangtise-data": {
      "command": "uvx",
      "args": [
        "--default-index",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "--with",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data",
        "--from",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data",
        "gangtise-data-mcp"
      ],
      "env": {
        "GTS_ACCESS_KEY": "YOUR_ACCESS_KEY",
        "GTS_SECRET_KEY": "YOUR_SECRET_KEY"
      }
    }
  }
}
```

**WorkBuddy**：将 MCP 配置 JSON 发给 WorkBuddy **智能体**由其安装；安装完成后打开侧边栏 **专家 · 技能 · 连接器** → 顶部 **连接器** → **自定义连接器** / **我的 MCP**，对 `gangtise_mcp` 依次 **信任** 并 **开启**（首次信任可能等待数秒）。推荐接入整合包，说明见 [`gangtise_mcp`](../gangtise_mcp/) 与 [仓库 README](../../README.cn.md)。


推荐包完整平台折叠示例见 [`gangtise_mcp`](../gangtise_mcp/) 与 [仓库 README](../../README.cn.md)。

</details>

<details>
<summary><b>远程 HTTP / Docker</b></summary>

- HTTP / SSE / OAuth：[docs/http-sse.cn.md](../../docs/http-sse.cn.md)
- Docker：仅整合镜像，见 [docker-deploy.cn.md](../../docs/docker-deploy.cn.md)

</details>


<details>
<summary><b>本地独立运行（开发）</b></summary>

本包为 **stdio** 业务入口；HTTP/SSE + 鉴权见 [`api/gangtise_data`](../../api/gangtise_data/)。在 monorepo 内：

```bash
cd gangtise-data-mcp/mcp/gangtise_data
uv sync
uv run gangtise-data-mcp          # stdio
# HTTP/SSE（api 包）
cd ../../api/gangtise_data && uv sync && uv run gangtise-data-api --transport both --host 0.0.0.0 --port 8000
```

CLI 调试见 [`cli/gangtise_data`](../../cli/gangtise_data/)。日常客户端接入推荐 [`gangtise_mcp`](../gangtise_mcp/)。

</details>

English: [README.md](README.md)
