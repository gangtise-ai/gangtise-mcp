# HTTP / SSE

**简体中文** | [English](http-sse.en.md)

远程 MCP 传输与鉴权说明（main）。客户端连接整合服务 `POST /mcp`。

---

## 传输

| 模式 | 端点 |
|------|------|
| streamable-http | `POST /mcp`（网关可为 `/mcp/{slug}`） |
| SSE | `GET /sse` + `POST /messages/` |

健康检查：`GET /health`。

兼容：响应回传 `X-DashScope-Request-ID`；`MCP_REQUIRE_AUTH=true` 时未带鉴权访问 `/mcp` 返回 **401**；工具参数 schema 为单层基本类型。

---

<details>
<summary><b>鉴权（Authorization 或 AK/SK）</b></summary>

支持两种方式（**Authorization 优先**）：

### 1. 直接传入 Authorization

HTTP：

```http
Authorization: Bearer <token>
```

stdio：环境变量 `GTS_AUTHORIZATION` / `AUTHORIZATION`，或本地文件：

```json
{"authorization": "Bearer <token>"}
```

### 2. AK/SK → loginV2 换票

HTTP：请求头传入凭证（JSON 或 Base64），例如：

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

亦可使用 `accessKey` / `secretKey` 头。

stdio / 进程环境：`GTS_ACCESS_KEY` + `GTS_SECRET_KEY`，或本地文件：

```json
{"accessKey":"<ak>","secretKey":"<sk>"}
```

换得的 `Authorization` 用于下游请求，以及 `get_white_list()` 权限查询。

</details>

<details>
<summary><b>客户端连接示例</b></summary>

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://<host>:<port>/mcp",
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
