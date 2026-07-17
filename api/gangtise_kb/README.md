<div align="center">

# Gangtise KB API

**简体中文** | [English](README.en.md)

知识库语义检索工具的远程 HTTP/SSE 入口。

> 本文假定服务**已经部署**。说明如何配置客户端连接本 HTTP/SSE 服务。  
> 业务实现见 [`mcp/gangtise_kb`](../../mcp/gangtise_kb/)。日常更推荐整合服务 [`gangtise_mcp`](../gangtise_mcp/)。

[仓库总览](../../README.md) · [鉴权与协议](../../docs/http-sse.md) · [Docker 部署](../../docs/docker-deploy.md) · [开放平台](https://open-platform.gangtise.com/)

</div>

---

## 端点

| 用途 | 地址 |
|------|------|
| MCP（streamable-http） | `https://<host>:<port>/mcp` |
| SSE | `GET /sse` + `POST /messages/` |
| 健康检查 | `GET /health` |

默认端口多为 `8000`（以实际部署为准）。响应会回传 `X-DashScope-Request-ID`。

---

## 工具

`kb`

---

<details>
<summary><b>鉴权（请求头）</b></summary>

**Authorization 优先**；也可使用 AK/SK。详见 [http-sse.md](../../docs/http-sse.md)。

```http
Authorization: Bearer <token>
```

或：

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

`MCP_REQUIRE_AUTH=true`（默认）时，访问 `/mcp` 必须带鉴权，否则返回 **401**。

</details>

<details>
<summary><b>客户端配置（Cursor 等，远程 URL）</b></summary>

将下方 JSON 中的地址与 token 换成实际部署值，发给 Cursor **Agent** 安装，或写入 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "gangtise-kb": {
      "url": "https://<host>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

百炼 / 其它仅支持 URL 的 MCP 客户端：填写同一 `/mcp` 地址，并在请求头携带 `Authorization`。

</details>

<details>
<summary><b>服务侧常用环境变量</b></summary>

| 变量 | 默认 | 说明 |
|------|------|------|
| `MCP_TRANSPORT` | `http`（镜像）/ 启动参数 | `http` / `sse` / `both` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | 监听地址 |
| `MCP_PATH` | `/mcp` | MCP 路径 |
| `MCP_REQUIRE_AUTH` | `true` | 缺鉴权返回 401 |
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | 空 | 进程级 AK/SK（无请求头时的兜底） |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | 工具 URL 白名单依赖图（镜像内） |

完整说明见 [docker-deploy.md](../../docs/docker-deploy.md)。

</details>

<details>
<summary><b>本机启动本 API（开发）</b></summary>

```bash
cd gangtise-data-mcp/api/gangtise_kb
uv sync
uv run gangtise-kb-api --transport both --host 0.0.0.0 --port 8000
```

本包**没有** `--transport stdio`；本地 stdio 请用对应 [`mcp/gangtise_kb`](../../mcp/gangtise_kb/)。

</details>

English: [README.en.md](README.en.md)
