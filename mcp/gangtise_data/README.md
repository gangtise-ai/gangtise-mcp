<div align="center">

# Gangtise Data MCP

[简体中文](README.cn.md) | **English**

Quotes, financials, valuation, fund flow, industry/company indicators, concepts, etc.

> **Recommended**: use the all-in-one package [`gangtise_mcp`](../gangtise_mcp/) for daily work. This page covers this package only.

[Repo overview](../../README.md) · [Credentials](https://open-platform.gangtise.com/)

</div>

---

## Tools

`block_constituents`, `company_indicator`, `concept`, `earning_forecast`, `financial`, `fund_flow`, `industry_indicator`, `main_business`, `quote`, `security`, `shareholder`, `valuation`

---

<details>
<summary><b>Install this package (Cursor)</b></summary>

Get keys from the [open platform](https://open-platform.gangtise.com/). Requires [uv](https://docs.astral.sh/uv/).

**Cursor** — Prefer sending the JSON below to the Cursor **Agent** to install (Accept when prompted); you can also write `~/.cursor/mcp.json` or project `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "gangtise-data": {
      "command": "uvx",
      "args": [
        "--with",
        "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_data",
        "--from",
        "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_data",
        "gangtise-data-mcp"
      ],
      "env": {
        "GTS_ACCESS_KEY": "YOUR_ACCESS_KEY",
        "GTS_SECRET_KEY": "YOUR_SECRET_KEY"
      }
    }
  }
}
```


Full platform folds: [`gangtise_mcp`](../gangtise_mcp/README.md) and [repo README](../../README.md).

</details>

<details>
<summary><b>Remote HTTP / Docker</b></summary>

- HTTP / SSE / OAuth: [http-sse.md](../../docs/http-sse.md)
- Docker: all-in-one only — [docker-deploy.md](../../docs/docker-deploy.md)

</details>


<details>
<summary><b>Run locally (dev)</b></summary>

This package is the **stdio** entry. HTTP/SSE + auth live in [`api/gangtise_data`](../../api/gangtise_data/):

```bash
cd gangtise-data-mcp/mcp/gangtise_data && uv sync && uv run gangtise-data-mcp   # stdio
cd ../../api/gangtise_data && uv sync && uv run gangtise-data-api --transport both --port 8000
```

CLI: [`cli/gangtise_data`](../../cli/gangtise_data/). Recommended client package: [`gangtise_mcp`](../gangtise_mcp/).

</details>

Chinese: [README.cn.md](README.cn.md)
