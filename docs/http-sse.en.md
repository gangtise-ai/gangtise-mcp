# HTTP / SSE

[简体中文](http-sse.md) | **English**

Remote MCP transport and auth (main). Clients connect to `POST /mcp`.

---

## Transport

| Mode | Endpoint |
|------|----------|
| streamable-http | `POST /mcp` (gateway may use `/mcp/{slug}`) |
| SSE | `GET /sse` + `POST /messages/` |

Health: `GET /health`.

Responses echo `X-DashScope-Request-ID`. With `MCP_REQUIRE_AUTH=true`, `/mcp` without auth returns **401**. Tool schemas are flattened.

---

<details>
<summary><b>Auth (Authorization or AK/SK)</b></summary>

Two modes (**Authorization wins** if both present):

### 1. Pass Authorization directly

HTTP:

```http
Authorization: Bearer <token>
```

stdio: `GTS_AUTHORIZATION` / `AUTHORIZATION`, or local file:

```json
{"authorization": "Bearer <token>"}
```

### 2. AK/SK → loginV2

HTTP credentials header (JSON or Base64), e.g.:

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

Or `accessKey` / `secretKey` headers.

stdio / env: `GTS_ACCESS_KEY` + `GTS_SECRET_KEY`, or local file with the same keys.

The resulting `Authorization` is used for downstream calls and for `get_white_list()`.

</details>

<details>
<summary><b>Client example</b></summary>

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

[Docker deploy](docker-deploy.en.md) · [Overview](../README.en.md)
