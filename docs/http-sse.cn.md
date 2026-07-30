# HTTP / SSE

**简体中文** | [English](http-sse.md)

远程 MCP 传输与鉴权说明（main）。生产 HTTP：`https://openapi.gangtise.com/application/mcp/` · SSE：`https://openapi.gangtise.com/application/mcp/sse`。服务内默认挂在根路径 `POST /`；网关前缀如 `/application/mcp`。可用 `MCP_PATH` 改服务内挂载路径。

---

## 传输

| 模式 | 端点 |
|------|------|
| streamable-http | 生产：`https://openapi.gangtise.com/application/mcp/` · 进程内：`POST /`（`MCP_PATH`） |
| SSE | 生产：`https://openapi.gangtise.com/application/mcp/sse` · 进程内：`GET /sse` + `POST /messages/` |

需 `MCP_TRANSPORT=sse` 或 `both`。经网关剥离前缀时，服务端下发**相对路径** `messages/?session_id=…`，客户端会解析到与 `/sse` 相同前缀下。

健康检查：`GET /health`。

兼容：响应回传 `X-DashScope-Request-ID`；`MCP_REQUIRE_AUTH=true` 时未带鉴权访问 MCP 路径返回 **401**；工具参数 schema 为单层基本类型。

---

<details open>
<summary><b>鉴权（推荐 AK/SK；亦可 Authorization）</b></summary>

两种方式可同时启用。**日常远程接入更建议直接传 AK/SK**（请求头 `accessKey` / `secretKey`，或 `X-GTS-Credentials`）。

解析优先级（服务端）：**直传 Authorization → AK/SK（含 X-GTS-Credentials）**。

### 1. AK/SK → loginV2（推荐）

开放平台申请 Access Key / Secret Key 后，在 MCP 客户端配置请求头即可：

```http
accessKey: <ak>
secretKey: <sk>
```

或：

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

stdio：`GTS_ACCESS_KEY` + `GTS_SECRET_KEY`（或本地 `~/.config/gangtise/authorization`）。

### 2. 直接传入 Authorization（业务 Bearer）

```http
Authorization: Bearer <token>
```

原样透传下游。stdio：`GTS_AUTHORIZATION` / 本地 authorization 文件。

</details>

<details open>
<summary><b>客户端连接示例</b>（`~/.cursor/mcp.json`）</summary>

`type` 需与 URL 一致：HTTP 基址用 `streamableHttp`，`/sse` 用 `sse`。不要把 `streamableHttp` 指到 `/sse`。

**Streamable HTTP（推荐）+ AK/SK**

```json
{
  "mcpServers": {
    "gangtise": {
      "type": "streamableHttp",
      "url": "https://openapi.gangtise.com/application/mcp/",
      "headers": {
        "accessKey": "<ak>",
        "secretKey": "<sk>"
      }
    }
  }
}
```

**SSE + AK/SK**

```json
{
  "mcpServers": {
    "gangtise-sse": {
      "type": "sse",
      "url": "https://openapi.gangtise.com/application/mcp/sse",
      "headers": {
        "accessKey": "<ak>",
        "secretKey": "<sk>"
      }
    }
  }
}
```

亦可使用 `X-GTS-Credentials`（`type` / `url` 同上）：

```json
{
  "mcpServers": {
    "gangtise": {
      "type": "streamableHttp",
      "url": "https://openapi.gangtise.com/application/mcp/",
      "headers": {
        "X-GTS-Credentials": "{\"accessKey\":\"<ak>\",\"secretKey\":\"<sk>\"}"
      }
    }
  }
}
```

直传业务 Token：

```json
{
  "mcpServers": {
    "gangtise": {
      "type": "streamableHttp",
      "url": "https://openapi.gangtise.com/application/mcp/",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

WorkBuddy 上架端点为 `https://openapi.gangtise.com/application/open-mcp/`（`streamableHttp`），见 [`connectors/workbuddy/gangtise-mcp/`](../connectors/workbuddy/gangtise-mcp/)。

</details>

---

[Docker 部署](docker-deploy.cn.md) · [CLI](cli.cn.md) · [总览](../README.cn.md)
