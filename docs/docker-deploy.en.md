# Docker deploy

[简体中文](docker-deploy.md) | **English**

All-in-one image (`mcps/Dockerfile`): `api/*` + `mcp/*`, HTTP deployment defaults (`MCP_LAYOUT=unified`, `MCP_TRANSPORT=http`, Authorization passthrough, flat schemas). Clients use **`/open-mcp`**. Protocol/auth: [http-sse.en.md](http-sse.en.md). Entrypoint: [`mcp/gangtise_mcp/entrypoint.sh`](../mcp/gangtise_mcp/entrypoint.sh).

---

<details>
<summary><b>Build and run</b></summary>

```bash
cd gangtise-data-mcp   # the mcps/ directory in the repo
docker build -t gangtise-mcp -f Dockerfile .

docker run -d --name gangtise-mcp -p 8000:8000 gangtise-mcp

curl -sS http://127.0.0.1:8000/health
```

Connect to `http://127.0.0.1:8000/open-mcp` with `Authorization: Bearer <token>` (forwarded as-is to downstream APIs).

</details>

<details>
<summary><b>Common env vars</b></summary>

| Variable | Default | Notes |
|----------|---------|--------|
| `MCP_TRANSPORT` | `http` | `http` / `sse` / `both` |
| `MCP_LAYOUT` | `unified` | `unified` / `gateway` |
| `MCP_PACKAGE` | `domains` | `domains` / `all` / single-domain slug |
| `MCP_REQUIRE_AUTH` | `true` | HTTP 401 if `/open-mcp` lacks `Authorization` |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | Build-time tool→URL dependency map |
| `GTS_MCP_ROOT` | `/opt/mcp` | contains `api/` and `mcp/` |
| `MCP_ATTACH_MAX_BYTES` | `33554432` | Inline attachment limit |
| `OBS_*` | empty | Optional large-attachment offload |

Tool visibility: build scans `*_URL` deps per tool; runtime `get_white_list()` (stub = all URLs) filters `tools/list` and `call`. Tools with no URL deps always stay; empty whitelist (banned user) hides every tool that has URL deps.

Responses echo `X-DashScope-Request-ID`. Tool schemas are flattened. No SPI / AK·SK / OAuth in this branch.

</details>

---

[HTTP / SSE](http-sse.en.md) · [Overview](../README.en.md)
