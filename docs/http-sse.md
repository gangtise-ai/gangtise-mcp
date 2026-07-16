# HTTP / SSE

**简体中文** | [English](http-sse.en.md)

远程 MCP 传输与鉴权说明（aliyun 内部分支）。客户端连接整合服务 `POST /mcp`。

---

## 传输

| 模式 | 端点 |
|------|------|
| streamable-http | `POST /mcp`（网关可为 `/mcp/{slug}`） |
| SSE | `GET /sse` + `POST /messages/` |

健康检查：`GET /health`。

百炼兼容：响应回传 `X-DashScope-Request-ID`；`MCP_REQUIRE_AUTH=true` 时未带 `Authorization` 访问 `/mcp` 返回 **401**；工具参数 schema 为单层基本类型。

---

<details>
<summary><b>鉴权（Authorization 透传）</b></summary>

本分支不再使用 AK/SK / loginV2 / 云市场 SPI。

HTTP：入站请求头 `Authorization: Bearer <token>` 原样转发至下游数据接口。

```http
Authorization: Bearer <token>
```

stdio：设置环境变量 `GTS_AUTHORIZATION`（或 `AUTHORIZATION`），或本地文件 `~/.config/gangtise/authorization`：

```json
{"authorization": "Bearer <token>"}
```

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

相关：[Docker 部署](docker-deploy.md) · [总览](../README.md)
