<div align="center">

# Gangtise Private MCP

[简体中文](README.cn.md) | **English**

Private data: drive, recordings, meetings, stock pools, WeChat messages.

> **Recommended**: use the all-in-one package [`gangtise_mcp`](../gangtise_mcp/) for daily work. This page covers this package only.

[Repo overview](../../README.md) · [Credentials](https://open-platform.gangtise.com/)

</div>

---

## Tools

`private_record`, `private_meeting`, `private_cloud`, `stockpool`, `wechat_message`

---

<details>
<summary><b>Install this package (Cursor)</b></summary>

Get keys from the [open platform](https://open-platform.gangtise.com/). Requires [uv](https://docs.astral.sh/uv/).

**Cursor** — Prefer sending the JSON below to the Cursor **Agent** to install (Accept when prompted); you can also write `~/.cursor/mcp.json` or project `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "gangtise-private": {
      "command": "uvx",
      "args": [
        "--with",
        "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_private",
        "--from",
        "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_private",
        "gangtise-private-mcp"
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

This package is the **stdio** entry. HTTP/SSE + auth live in [`api/gangtise_private`](../../api/gangtise_private/):

```bash
cd gangtise-data-mcp/mcp/gangtise_private && uv sync && uv run gangtise-private-mcp   # stdio
cd ../../api/gangtise_private && uv sync && uv run gangtise-private-api --transport both --port 8000
```

CLI: [`cli/gangtise_private`](../../cli/gangtise_private/). Recommended client package: [`gangtise_mcp`](../gangtise_mcp/).

</details>

Chinese: [README.cn.md](README.cn.md)
