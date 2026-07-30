# Docker deploy

[简体中文](docker-deploy.md) | **English**

All-in-one image (`mcps/Dockerfile`): `api/*` + `mcp/*`, HTTP deployment defaults (`MCP_LAYOUT=unified`, `MCP_TRANSPORT=http`, Authorization passthrough, flat schemas). Clients use **`/`** by default (`MCP_PATH`); gateways may prefix e.g. `/application/open-mcp`. Protocol/auth: [http-sse.en.md](http-sse.en.md). Entrypoint: [`mcp/gangtise_mcp/entrypoint.sh`](../mcp/gangtise_mcp/entrypoint.sh).

---

<details>
<summary><b>Build and run</b></summary>

```bash
cd gangtise-data-mcp   # the mcps/ directory in the repo
docker build -t gangtise-mcp -f Dockerfile .

docker run -d --name gangtise-mcp -p 8000:8000 gangtise-mcp

curl -sS http://127.0.0.1:8000/health
```

Connect to `http://127.0.0.1:8000/` with `Authorization: Bearer <token>` (forwarded as-is to downstream APIs).

</details>

<details>
<summary><b>Common env vars</b></summary>

| Variable | Default | Notes |
|----------|---------|--------|
| `MCP_TRANSPORT` | `http` | `http` / `sse` / `both` |
| `MCP_LAYOUT` | `unified` | `unified` / `gateway` |
| `MCP_PACKAGE` | `domains` | `domains` / `all` / single-domain slug |
| `MCP_PATH` | `/` | In-process MCP mount; empty = root |
| `MCP_REQUIRE_AUTH` | `true` | HTTP 401 if MCP path lacks `Authorization` |
| `MCP_TOOL_BLACKLIST` | empty | Comma-separated tool names; hidden from `tools/list` and rejected on `call` |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | Build-time tool→API path dependency map |
| `MCP_API_GETLIST_PATH` | `/api/getList` | User API whitelist path (appended to `GANGTISE_DATA_DOMAIN`) |
| `MCP_WHITELIST_CACHE_SEC` | `300` | getList cache TTL seconds |
| `MCP_WHITELIST_STRICT` | `false` | If `true`, getList failure yields empty whitelist; default falls back to all paths |
| `GTS_MCP_ROOT` | `/opt/mcp` | contains `api/` and `mcp/` |
| `MCP_ATTACH_MAX_BYTES` | `33554432` | Inline attachment limit; larger files use OBS when configured |
| `MCP_ATTACH_OBS_ALWAYS` | `false` | When `true`, always upload to OBS and put download URLs in text (no embedded blobs; for WorkBuddy) |
| `OBS_*` | empty | Optional OBS offload: `OBS_ACCESS_KEY` / `SECRET_KEY` / `ENDPOINT` / `BUCKET` / `PATH` |
| `OBS_EXPIRE_DAYS` | `1` | OBS object lifetime in days (auto-delete) |

Tool visibility: `MCP_TOOL_BLACKLIST` wins first (absolute hide). Then build-time API-path deps + runtime `get_white_list()` (`GET {GANGTISE_DATA_DOMAIN}/api/getList`) filter `tools/list` and `call`. Tools with no path deps (and not blacklisted) stay; empty whitelist (banned user / strict getList failure) hides every tool that has path deps.

Responses echo `X-DashScope-Request-ID`. Tool schemas are flattened. No SPI / AK·SK / OAuth in this branch.

</details>

---

[HTTP / SSE](http-sse.en.md) · [Overview](../README.en.md)
