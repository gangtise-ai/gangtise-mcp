<div align="center">

# Gangtise Agent API

[简体中文](README.md) | **English**

Remote HTTP/SSE entry for research Agent narrative tools.

> This page assumes the service is **already deployed**. It covers how to configure clients.  
> Implementation: [`mcp/gangtise_agent`](../../mcp/gangtise_agent/). Prefer all-in-one [`gangtise_mcp`](../gangtise_mcp/).

[Repo overview](../../README.en.md) · [Auth / protocol](../../docs/http-sse.en.md) · [Docker](../../docs/docker-deploy.en.md) · [Open Platform](https://open-platform.gangtise.com/)

</div>

---

## Endpoints

| Purpose | URL |
|---------|-----|
| MCP (streamable-http) | `https://<host>:<port>/mcp` |
| SSE | `GET /sse` + `POST /messages/` |
| Health | `GET /health` |

Default port is often `8000`. Responses echo `X-DashScope-Request-ID`.

---

## Tools

`stock_one_pager`, `investment_logic`, `peer_comparison`, `earnings_review`, `viewpoint_debate`, `theme_tracking`, `research_outline`, `stock_one_line_summary`, `hot_topic`, `security_clue`

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

With `MCP_REQUIRE_AUTH=true` (default), `/mcp` without auth returns **401**.

</details>

<details>
<summary><b>Client config (Cursor, remote URL)</b></summary>

Replace host/token, then send to Cursor **Agent** or write `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "gangtise-agent": {
      "url": "https://<host>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

Bailian / other URL-only MCP clients: use the same `/mcp` URL and pass `Authorization`.

</details>

<details>
<summary><b>Server env vars</b></summary>

| Variable | Default | Notes |
|----------|---------|--------|
| `MCP_TRANSPORT` | `http` (image) | `http` / `sse` / `both` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | Bind address |
| `MCP_PATH` | `/mcp` | MCP path |
| `MCP_REQUIRE_AUTH` | `true` | 401 without auth |
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | empty | Process-level AK/SK fallback |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | Tool URL dependency map (image) |

See [docker-deploy.en.md](../../docs/docker-deploy.en.md).

</details>

<details>
<summary><b>Run this API locally (dev)</b></summary>

```bash
cd gangtise-data-mcp/api/gangtise_agent
uv sync
uv run gangtise-agent-api --transport both --host 0.0.0.0 --port 8000
```

No `--transport stdio` here; use [`mcp/gangtise_agent`](../../mcp/gangtise_agent/) for stdio.

</details>

Chinese: [README.md](README.md)
