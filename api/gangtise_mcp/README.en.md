<div align="center">

# Gangtise MCP API (All-in-one)

[简体中文](README.md) | **English**

Remote HTTP/SSE entry exposing all leaf tools across five domains. Prefer this for remote clients.

> This page assumes the service is **already deployed**. It covers how to configure clients.  
> The all-in-one Docker image runs this service by default (`MCP_LAYOUT=unified`).

[Repo overview](../../README.en.md) · [Auth / protocol](../../docs/http-sse.en.md) · [Docker](../../docs/docker-deploy.en.md) · [Open Platform](https://open-platform.gangtise.com/)

</div>

---

## Endpoints

| Purpose | URL |
|---------|-----|
| MCP (streamable-http) | `https://<host>:<port>/open-mcp` |
| SSE | `GET /sse` + `POST /messages/` |
| Health | `GET /health` |

Default port is often `8000`. Responses echo `X-DashScope-Request-ID`.

---

## Tools

All leaf tools (~45) across five domains; see [`mcp/gangtise_mcp`](../../mcp/gangtise_mcp/).

---

<details>
<summary><b>Auth (request headers)</b></summary>

**Authorization preferred**; AK/SK also supported. See [http-sse.en.md](../../docs/http-sse.en.md).

```http
Authorization: Bearer <token>
```

Or:

```http
X-GTS-Credentials: {"accessKey":"<ak>","secretKey":"<sk>"}
```

With `MCP_REQUIRE_AUTH=true` (default), `/open-mcp` without auth returns **401**.

</details>

<details>
<summary><b>Client config (Cursor, remote URL)</b></summary>

Replace host/token, then send to Cursor **Agent** or write `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "gangtise": {
      "url": "https://<host>:<port>/open-mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

URL-based MCP clients: use the same `/open-mcp` URL and pass `Authorization`.

</details>

<details>
<summary><b>Server env vars</b></summary>

| Variable | Default | Notes |
|----------|---------|--------|
| `MCP_TRANSPORT` | `http` (image) | `http` / `sse` / `both` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | Bind address |
| `MCP_PATH` | `/open-mcp` | MCP path |
| `MCP_REQUIRE_AUTH` | `true` | 401 without auth |
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | empty | Process-level AK/SK fallback |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | Tool URL dependency map (image) |

See [docker-deploy.en.md](../../docs/docker-deploy.en.md).

</details>

<details>
<summary><b>Run this API locally (dev)</b></summary>

```bash
cd gangtise-data-mcp/api/gangtise_mcp
uv sync
uv run gangtise-mcp-api --transport both --host 0.0.0.0 --port 8000
```

No `--transport stdio` here; use [`mcp/gangtise_mcp`](../../mcp/gangtise_mcp/) for stdio.

</details>

Chinese: [README.md](README.md)
