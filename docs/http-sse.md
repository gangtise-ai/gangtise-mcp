# HTTP / SSE

**简体中文** | [English](http-sse.en.md)

远程 MCP 传输与鉴权说明（main）。服务默认挂在根路径 `POST /`；线上网关常再加前缀（如 `/application/open-mcp`）。可用环境变量 `MCP_PATH` 改服务内挂载路径。

---

## 传输

| 模式 | 端点 |
|------|------|
| streamable-http | `POST /`（默认；`MCP_PATH` 可改，如 `/open-mcp`）。网关 layout 为 `{MCP_PATH}/{slug}` |
| SSE | `GET /sse` + `POST /messages/` |

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
<summary><b>客户端连接示例</b></summary>

**推荐：请求头传 AK/SK**

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/open-mcp/",
      "headers": {
        "accessKey": "<ak>",
        "secretKey": "<sk>"
      }
    }
  }
}
```

亦可使用 `X-GTS-Credentials`：

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/open-mcp/",
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
      "url": "https://openapi.gangtise.com/application/open-mcp/",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

</details>

---

[Docker 部署](docker-deploy.md) · [总览](../README.md)
