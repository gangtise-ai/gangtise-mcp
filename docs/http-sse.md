# HTTP / SSE

[简体中文](http-sse.cn.md) | **English**

Remote MCP transport and auth (main). Production HTTP base: `https://openapi.gangtise.com/application/mcp/` · SSE: `https://openapi.gangtise.com/application/mcp/sse`. In-process default is root `POST /`; gateways add a prefix (e.g. `/application/mcp`). Set `MCP_PATH` to change the in-process path.

---

## Transport

| Mode | Endpoint |
|------|----------|
| streamable-http | Production: `https://openapi.gangtise.com/application/mcp/` · In-process: `POST /` (`MCP_PATH`) |
| SSE | Production: `https://openapi.gangtise.com/application/mcp/sse` · In-process: `GET /sse` + `POST /messages/` |

Health: `GET /health`.

Responses echo `X-DashScope-Request-ID`. With `MCP_REQUIRE_AUTH=true`, the MCP path without auth returns **401**. Tool schemas are flattened.

---

<details open>
<summary><b>Auth (prefer AK/SK; Authorization also supported)</b></summary>

Two modes can be enabled together. **For everyday remote use, prefer passing AK/SK** (`accessKey` / `secretKey` headers, or `X-GTS-Credentials`).

Server resolution order: **raw Authorization → AK/SK (incl. X-GTS-Credentials)**.

### 1. AK/SK → loginV2 (recommended)

After obtaining Access Key / Secret Key from the open platform, set request headers in the MCP client:

```http
accessKey: <ak>
secretKey: <sk>
```

Or:

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

stdio: `GTS_ACCESS_KEY` + `GTS_SECRET_KEY` (or local `~/.config/gangtise/authorization`).

### 2. Pass Authorization (business Bearer)

```http
Authorization: Bearer <token>
```

Pass-through. stdio: `GTS_AUTHORIZATION` or local file.

</details>

<details open>
<summary><b>Client example</b></summary>

**Recommended: AK/SK headers**

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/mcp/",
      "headers": {
        "accessKey": "<ak>",
        "secretKey": "<sk>"
      }
    }
  }
}
```

Or `X-GTS-Credentials`:

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/mcp/",
      "headers": {
        "X-GTS-Credentials": "{\"accessKey\":\"<ak>\",\"secretKey\":\"<sk>\"}"
      }
    }
  }
}
```

Bearer pass-through:

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://openapi.gangtise.com/application/mcp/",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

</details>

---

[Docker](docker-deploy.md) · [CLI](cli.md) · [Overview](../README.md)
