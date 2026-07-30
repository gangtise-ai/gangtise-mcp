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

<details>
<summary><b>鉴权（OAuth / Authorization / X-GTS-Credentials）</b></summary>

三种方式可同时启用（**优先级：本服务 OAuth JWT → 直传 Authorization → X-GTS-Credentials**）：

### 1. OAuth 2.1（WorkBuddy / 浏览器同意页）

配置环境变量后启用：

| 变量 | 说明 |
|------|------|
| `GTS_JWT_SECRET` | 必填；JWT 签名密钥 |
| `GTS_CRED_ENC_KEY` | 可选；Fernet key，缺省由 JWT secret 派生 |
| `GTS_OAUTH_ISSUER` | 对外 issuer（反代完整 URL，无尾斜杠），如 `https://openapi.gangtise.com/application/open-mcp` |

端点：

| 路径 | 说明 |
|------|------|
| `GET /.well-known/oauth-protected-resource` | 资源元数据 |
| `GET /.well-known/oauth-authorization-server` | AS 元数据 |
| `POST /oauth/register`（别名 `/register`） | 动态客户端注册 |
| `GET/POST /oauth/authorize`（别名 `/authorize`） | 同意页：用户填 AK/SK，loginV2 校验后发 code |
| `POST /oauth/token`（别名 `/token`） | code / refresh_token 换 access（1h）+ refresh（7d） |

未带鉴权访问 MCP 路径时返回 **401**，并带 `WWW-Authenticate: Bearer FAKESECRET_g3h4i5j6k7l8m9n0o1p2="…"`。  
客户端持有的 access JWT 在网关内解出 AK/SK，再 **loginV2** 调下游（不会把 MCP JWT 透传给业务 API）。

### 2. 直接传入 Authorization（业务 Bearer）

```http
Authorization: Bearer <token>
```

原样透传下游。stdio：`GTS_AUTHORIZATION` / 本地 authorization 文件。

### 3. AK/SK → loginV2

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

亦可 `accessKey` / `secretKey` 头。stdio：`GTS_ACCESS_KEY` + `GTS_SECRET_KEY`。

</details>

<details>
<summary><b>客户端连接示例</b></summary>

OAuth（客户端自动完成，无需 headers）：

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/open-mcp/"
    }
  }
}
```

直传 Token：

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
