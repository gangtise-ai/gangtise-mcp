<div align="center">

# Gangtise Hub MCP

[简体中文](README.cn.md) | **English**

Exposes five domain routers only; discover leaf tools via list / read_ref / call (progressive disclosure).

> **Recommended**: use the all-in-one package [`gangtise_mcp`](../gangtise_mcp/) for daily work. This page covers this package only.

[Repo overview](../../README.md) · [Credentials](https://open-platform.gangtise.com/)

</div>

---

## Tools

`gangtise-data`, `gangtise-file`, `gangtise-agent`, `gangtise-kb`, `gangtise-private` (routers)

---

<details>
<summary><b>Install this package (Cursor)</b></summary>

Get keys from the [open platform](https://open-platform.gangtise.com/). Requires [uv](https://docs.astral.sh/uv/).

**Cursor** — Prefer sending the JSON below to the Cursor **Agent** to install (Accept when prompted); you can also write `~/.cursor/mcp.json` or project `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "gangtise-hub": {
      "command": "uvx",
      "args": [
        "--with",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_hub",
        "--from",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_hub",
        "gangtise-hub-mcp"
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

This package is the **stdio** entry. HTTP/SSE + auth live in [`api/gangtise_hub`](../../api/gangtise_hub/):

```bash
cd gangtise-data-mcp/mcp/gangtise_hub && uv sync && uv run gangtise-hub-mcp   # stdio
cd ../../api/gangtise_hub && uv sync && uv run gangtise-hub-api --transport both --port 8000
```

CLI: [`cli/gangtise_hub`](../../cli/gangtise_hub/). Recommended client package: [`gangtise_mcp`](../gangtise_mcp/).

</details>

Chinese: [README.cn.md](README.cn.md)
