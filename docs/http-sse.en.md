# HTTP / SSE

[简体中文](http-sse.md) | **English**

Remote MCP transport and auth (main). Default mount is root `POST /`; gateways often add a prefix (e.g. `/application/open-mcp`). Set `MCP_PATH` to change the in-process path.

---

## Transport

| Mode | Endpoint |
|------|----------|
| streamable-http | `POST /` (default; override with `MCP_PATH`). Gateway layout uses `{MCP_PATH}/{slug}` |
| SSE | `GET /sse` + `POST /messages/` |

Health: `GET /health`.

Responses echo `X-DashScope-Request-ID`. With `MCP_REQUIRE_AUTH=true`, the MCP path without auth returns **401**. Tool schemas are flattened.

---

<details>
<summary><b>Auth (OAuth / Authorization / X-GTS-Credentials)</b></summary>

Three modes can be enabled together (**priority: MCP OAuth JWT → raw Authorization → X-GTS-Credentials**):

### 1. OAuth 2.1 (WorkBuddy / browser consent)

| Env | Notes |
|-----|--------|
| `GTS_JWT_SECRET` | Required to enable OAuth |
| `GTS_CRED_ENC_KEY` | Optional Fernet key; derived from JWT secret if omitted |
| `GTS_OAUTH_ISSUER` | Public issuer URL (no trailing slash), e.g. `https://openapi.gangtise.com/application/open-mcp` |

Endpoints: `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`, `/oauth/register`, `/oauth/authorize`, `/oauth/token` (aliases `/register`, `/authorize`, `/token`).

Unauthenticated MCP calls return **401** with `WWW-Authenticate` `resource_metadata`. Access JWTs are resolved to AK/SK inside the gateway, then **loginV2** for downstream APIs.

### 2. Pass Authorization (business Bearer)

```http
Authorization: Bearer <token>
```

Pass-through. stdio: `GTS_AUTHORIZATION` or local file.

### 3. AK/SK → loginV2

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

Or `accessKey` / `secretKey` headers. stdio: `GTS_ACCESS_KEY` + `GTS_SECRET_KEY`.

</details>

<details>
<summary><b>Client example</b></summary>

OAuth (no headers; client runs the flow):

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/open-mcp/"
    }
  }
}
```

Bearer pass-through:

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

[Docker deploy](docker-deploy.en.md) · [Overview](../README.en.md)
