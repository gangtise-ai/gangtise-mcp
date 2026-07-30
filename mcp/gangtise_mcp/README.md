<div align="center">

# Gangtise MCP (Recommended)

[简体中文](README.cn.md) | **English**

All leaf tools from five domains (~45) in one server. Prefer this package for daily use; single-domain / hub packages are listed in the [repo overview](../../README.md).

[Credentials](https://open-platform.gangtise.com/) · [HTTP / SSE](../../docs/http-sse.md) · [Docker](../../docs/docker-deploy.md)

</div>

---

## Quick Start

1. Get **AK / SK** from the [open platform](https://open-platform.gangtise.com/).
2. Install [uv](https://docs.astral.sh/uv/).
3. Wire `gangtise-mcp` into your client (examples below use GitHub).

---

## Install by platform

> **Tip**: On clients with an agent, prefer pasting the MCP JSON into the agent to install. Full illustrated flows: [repo README](../../README.md).

<details>
<summary><b>Install in Cursor</b></summary>

Prefer: send JSON to the Cursor **Agent** → **Accept** when prompted → **Cursor → Preferences → Cursor Settings** → **Tools & MCP** to check status and enable if needed. Screenshots: [repo README](../../README.md). Or write `~/.cursor/mcp.json` manually:

```json
{
  "mcpServers": {
    "gangtise": {
      "command": "uvx",
      "args": [
                "--with",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_agent",
        "--with",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data",
        "--with",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_file",
        "--with",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_kb",
        "--with",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private",
        "--from",
        "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp",
        "gangtise-mcp"
      ],
      "env": {
        "GTS_ACCESS_KEY": "YOUR_ACCESS_KEY",
        "GTS_SECRET_KEY": "YOUR_SECRET_KEY"
      }
    }
  }
}
```

</details>


<details>
<summary><b>Install in WorkBuddy</b></summary>

Marketplace Connector package (OAuth): [`connectors/workbuddy/gangtise-mcp/`](../../connectors/workbuddy/gangtise-mcp/). For ad-hoc install, send the same MCP JSON as Cursor (server key may be `gangtise_mcp`) to the WorkBuddy agent, then **trust** and **enable** the connector.

</details>

<details>
<summary><b>Install in Claude Desktop / Claude Code / VS Code</b></summary>

Prefer sending the MCP JSON to each platform’s agent. Same patterns as the [repo README “Install by platform”](../../README.md#install-by-platform-recommended-gangtise_mcp): command `gangtise-mcp`, subdirectories `api/gangtise_mcp` + `mcp/gangtise_mcp` (GitHub).

</details>

<details>
<summary><b>Remote HTTP / SSE</b></summary>

```
https://<host>:<port>/open-mcp
```

OAuth or `X-GTS-Credentials`: [http-sse.md](../../docs/http-sse.md).

</details>

<details>
<summary><b>uvx CLI</b></summary>

```bash
uvx \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_agent" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_file" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_kb" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  gangtise-mcp
```

</details>

---

<details>
<summary><b>Tools</b></summary>

All five-domain leaf tools (`quote`, `report`, `kb`, `stock_one_pager`, `stockpool`, …).  
For progressive disclosure use [gangtise_hub](../gangtise_hub/); for one domain use the matching `mcp/gangtise_*` package.

</details>

<details>
<summary><b>Docker</b></summary>

All-in-one image only:

```bash
cd gangtise-data-mcp
docker build -t gangtise-mcp -f Dockerfile .
```

Set `GTS_JWT_SECRET` / `GTS_CRED_ENC_KEY` for OAuth. See [docker-deploy.md](../../docs/docker-deploy.md).

</details>


<details>
<summary><b>Run locally (dev)</b></summary>

This MCP package depends on sibling [`api/gangtise_mcp`](../../api/gangtise_mcp/):

```bash
cd gangtise-data-mcp/mcp/gangtise_mcp
uv sync
uv run gangtise-mcp --help
uv run gangtise-mcp                          # stdio
uv run gangtise-mcp --transport http --port 8000
```

CLI: [`cli/gangtise_mcp`](../../cli/gangtise_mcp/). Recommended client package: [`gangtise_mcp`](../gangtise_mcp/).

</details>

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
