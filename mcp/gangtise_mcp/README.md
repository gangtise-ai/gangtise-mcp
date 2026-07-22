<div align="center">

# Gangtise MCP（推荐）

**简体中文** | [English](README.en.md)

一次挂载五域全部叶子工具（约 45 个）。日常优先本包；单域 / Hub 见 [仓库总览](../../README.md)。

[开放平台](https://open-platform.gangtise.com/) · [HTTP / SSE](../../docs/http-sse.md) · [Docker](../../docs/docker-deploy.md)

</div>

---

## 快速开始

1. 在 [开放平台](https://open-platform.gangtise.com/) 获取 **AK / SK**。
2. 安装 [uv](https://docs.astral.sh/uv/)。
3. 按下列折叠项接入客户端（默认示例均为本包 `gangtise-mcp`，仓库为 Gitee）。

---

## 按平台安装

> **建议**：带智能体的客户端优先将下方 MCP JSON 发给智能体安装；亦可手动写入配置文件。完整截图流程见 [仓库 README](../../README.md)。

<details>
<summary><b>Install in Cursor</b></summary>

推荐：将下方 JSON 发给 Cursor **Agent** 安装 → 提示时 **Accept** → **Cursor → Preferences → Cursor Settings** → **Tools & MCP** 查看状态，未开启则开启。截图流程见 [仓库 README](../../README.md)。亦可手动写入 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "gangtise": {
      "command": "uvx",
      "args": [
                "--default-index",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "--with",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_agent",
        "--with",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data",
        "--with",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_file",
        "--with",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_kb",
        "--with",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private",
        "--from",
        "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp",
        "gangtise-mcp"
      ],
      "env": {
        "GTS_ACCESS_KEY": "YOUR_ACCESS_KEY",
        "GTS_SECRET_KEY": "YOUR_SECRET_KEY"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Install in WorkBuddy（腾讯云代码助手）</b></summary>

官方：[WorkBuddy MCP 指南](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)。

市场 Connector 提交包（OAuth）：[`connectors/workbuddy/gangtise-mcp/`](../../connectors/workbuddy/gangtise-mcp/)。

1. 将 MCP 配置 JSON（与上方 Cursor 示例相同，server 键名可用 `gangtise_mcp`；先填好 ak/sk）发给 WorkBuddy **智能体**，请其完成安装。
2. 安装完成后，打开侧边栏 **专家 · 技能 · 连接器** → 顶部 **连接器** → **自定义连接器** / **我的 MCP**，对 `gangtise_mcp` 依次 **信任** 并 **开启**（首次信任可能等待数秒）。

</details>

<details>
<summary><b>Install in Claude Desktop / Claude Code / VS Code</b></summary>

同样建议将 MCP JSON 发给对应平台的智能体完成安装。写法与 [仓库 README「按平台安装」](../../README.md#按平台安装推荐gangtise_mcp) 相同，命令均为 `gangtise-mcp`，子目录 `api/gangtise_mcp` + `mcp/gangtise_mcp`（Gitee）。

</details>

<details>
<summary><b>远程 HTTP / SSE</b></summary>

```
https://<host>:<port>/open-mcp
```

OAuth 或 `X-GTS-Credentials` 见 [docs/http-sse.md](../../docs/http-sse.md)。

</details>

<details>
<summary><b>命令行 uvx</b></summary>

```bash
uvx --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_agent" \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data" \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_file" \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_kb" \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private" \
  --from "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  gangtise-mcp
```

</details>

---

<details>
<summary><b>工具范围</b></summary>

五域叶子工具一并暴露（如 `quote`、`report`、`kb`、`stock_one_pager`、`stockpool` 等）。  
渐进披露用 [gangtise_hub](../gangtise_hub/)；仅某一域用对应 `mcp/gangtise_*` 包。

HTTP/SSE 见 [`api/gangtise_mcp`](../../api/gangtise_mcp/)（`gangtise-mcp-api`）；业务仍依赖五域 mcp 包。

</details>

<details>
<summary><b>Docker</b></summary>

仅整合镜像（示例使用清华 pip）：

```bash
cd gangtise-data-mcp
docker build -t gangtise-mcp -f Dockerfile \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
  .
```

运行时建议设置 `GTS_JWT_SECRET` / `GTS_CRED_ENC_KEY`。详见 [docker-deploy.md](../../docs/docker-deploy.md)。

</details>


<details>
<summary><b>本地独立运行（开发）</b></summary>

本包为 **stdio** 全量叶子入口；HTTP/SSE / gateway 见 [`api/gangtise_mcp`](../../api/gangtise_mcp/)。

```bash
cd gangtise-data-mcp/mcp/gangtise_mcp
uv sync
# stdio（默认）
uv run gangtise-mcp
```

CLI 调试见 [`cli/gangtise_mcp`](../../cli/gangtise_mcp/)。日常客户端接入推荐 [`gangtise_mcp`](../gangtise_mcp/)。

</details>

English: [README.en.md](README.en.md) · 总览: [../../README.md](../../README.md)
