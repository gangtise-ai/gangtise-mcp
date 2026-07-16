# HTTP / SSE

[简体中文](http-sse.md) | **English**

Remote MCP transport and auth (aliyun internal branch). Clients connect with `POST /mcp`.

---

## Transport

| Mode | Endpoint |
|------|----------|
| streamable-http | `POST /mcp` (gateway may use `/mcp/{slug}`) |
| SSE | `GET /sse` + `POST /messages/` |

Health: `GET /health`.

Bailian: responses echo `X-DashScope-Request-ID`; with `MCP_REQUIRE_AUTH=true`, missing `Authorization` on `/mcp` returns **401**; tool schemas are flat.

---

<details>
<summary><b>Auth (Authorization passthrough)</b></summary>

This branch does **not** use AK/SK, loginV2, or Cloud Market SPI.

HTTP: inbound `Authorization: Bearer <token>` is forwarded as-is to downstream data APIs.

stdio: set `GTS_AUTHORIZATION` (or `AUTHORIZATION`), or a local file `~/.config/gangtise/authorization`:

```json
{"authorization": "Bearer <token>"}
```

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
