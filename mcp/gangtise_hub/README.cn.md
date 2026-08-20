<div align="center">

# Gangtise Hub MCP

**简体中文** | [English](README.md)

`tools/list` 仅暴露五个域入口；再经 list / read_ref / call 调用叶子工具（渐进披露）。

> **日常推荐**：请优先使用整合包 [`gangtise_mcp`](../gangtise_mcp/)（五域全部叶子工具）。本页仅介绍本单包。

[仓库总览](../../README.cn.md) · [开放平台](https://open-platform.gangtise.com/)

</div>

---

## 工具

`gangtise-data`, `gangtise-file`, `gangtise-agent`, `gangtise-kb`, `gangtise-private`（路由器）

---

<details>
<summary><b>本包安装（Cursor / WorkBuddy）</b></summary>

账号：[开放平台](https://open-platform.gangtise.com/)。需安装 [uv](https://docs.astral.sh/uv/)。

**Cursor** — 推荐将下方 JSON 发给 Cursor **Agent** 由其安装（改动出现时 Accept），再在 Settings → Tools & MCP 确认；亦可手动写入 `~/.cursor/mcp.json` 或项目 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "gangtise-hub": {
      "command": "uvx",
      "args": [
        "--default-index",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "--with",
        "git+https://gitee.com/gangtise/gangtise-mcp#subdirectory=mcp/gangtise_hub",
        "--from",
        "git+https://gitee.com/gangtise/gangtise-mcp#subdirectory=mcp/gangtise_hub",
        "gangtise-hub-mcp"
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

本包为 **stdio** 业务入口；HTTP/SSE + 鉴权见 [`api/gangtise_hub`](../../api/gangtise_hub/)。在 monorepo 内：

```bash
cd gangtise-data-mcp/mcp/gangtise_hub
uv sync
uv run gangtise-hub-mcp          # stdio
# HTTP/SSE（api 包）
cd ../../api/gangtise_hub && uv sync && uv run gangtise-hub-api --transport both --host 0.0.0.0 --port 8000
```

CLI 调试见 [`cli/gangtise_hub`](../../cli/gangtise_hub/)。日常客户端接入推荐 [`gangtise_mcp`](../gangtise_mcp/)。

</details>

English: [README.md](README.md)
