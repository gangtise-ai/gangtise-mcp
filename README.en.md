<div align="center">

# Gangtise MCP

[简体中文](README.md) | **English**

Gangtise financial data and research tools over the [Model Context Protocol](https://modelcontextprotocol.io/).  
**Recommended package: [`gangtise_mcp`](mcp/gangtise_mcp/)** — all leaf tools from five domains in one server.

[Get credentials](https://open-platform.gangtise.com/) ·
[HTTP / SSE / OAuth](docs/http-sse.en.md) ·
[Docker](docs/docker-deploy.en.md)

</div>

---

## Quick Start

1. Create an account on the [open platform](https://open-platform.gangtise.com/) and get **Access Key / Secret Key** from account settings.
2. Install [uv](https://docs.astral.sh/uv/) for local stdio.
3. Wire the **recommended** package `gangtise-mcp` into your client (below).

### Connection modes

| Mode | Notes |
|------|--------|
| **Local stdio** | Run via `uvx`; set AK/SK in env |
| **Remote HTTP / SSE** | Connect to `/mcp`; use **OAuth consent** (Bearer only) or send AK/SK in headers |

Repository used in examples below: [`https://github.com/XiaoYan3938/gangtise-data-mcp`](https://github.com/XiaoYan3938/gangtise-data-mcp). Chinese docs use Gitee — see [README.md](README.md).

---

## Install by platform (recommended: `gangtise_mcp`)

> **Tip**: On clients with an agent (Cursor, Claude, VS Code Copilot/Agent, etc.), prefer pasting the MCP JSON below into the agent and letting it install. You can also write the same JSON into the client config file manually.

<details>
<summary><b>Install in Cursor</b></summary>

1. Paste the MCP JSON below into the Cursor **Agent** (optionally with real `GTS_ACCESS_KEY` / `GTS_SECRET_KEY`) and ask it to install:


<details>
<summary><b>MCP config JSON</b> (expand to copy)</summary>

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

![Send MCP JSON to the Cursor Agent](assets/init_with_cursor.png)

2. When prompted to confirm edits, click **Accept**:

![Accept Agent changes to mcp.json](assets/accept_edit.png)

3. Open settings via **Cursor → Preferences → Cursor Settings**:

![Open Cursor Settings](assets/check_mcp.png)

4. Go to **Tools & MCP**, check the install status; turn it on if it is not enabled (first start may take time to pull deps):

![Check status and enable under Tools & MCP](assets/check_mcp_status.png)

You can also edit `~/.cursor/mcp.json` or project `.cursor/mcp.json` manually (same content). Remote URL: [docs/http-sse.en.md](docs/http-sse.en.md).

</details>


<details>
<summary><b>Install in Claude Desktop</b></summary>

Prefer pasting the JSON below into the Claude **agent / chat** and asking it to write the MCP config. You can also edit `claude_desktop_config.json` manually. Requires [uv](https://docs.astral.sh/uv/):

<details>
<summary><b>MCP config JSON</b> (expand to copy)</summary>

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

</details>

<details>
<summary><b>Install in Claude Code</b></summary>

Prefer sending the same MCP JSON (as Cursor / Claude Desktop) to the Claude Code **agent** to install. CLI alternative:

```bash
claude mcp add gangtise -- uvx \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_agent" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_file" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_kb" \
  --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  gangtise-mcp
```

Set `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` in the MCP env or your shell.

</details>

<details>
<summary><b>Install in VS Code</b></summary>

Prefer pasting the JSON below into Copilot Chat / Agent and asking it to write workspace `.vscode/mcp.json`. You can also create the file manually:

<details>
<summary><b>MCP config JSON</b> (expand to copy)</summary>

```json
{
  "servers": {
    "gangtise": {
      "type": "stdio",
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

For remote HTTP, set `"type": "http"` and `"url": "https://<host>:<port>/mcp"`.

</details>

<details>
<summary><b>Remote HTTP / SSE (OAuth or headers)</b></summary>

Deploy with [docs/docker-deploy.en.md](docs/docker-deploy.en.md). Client URL:

```
https://<host>:<port>/mcp
```

- **OAuth**: set `GTS_JWT_SECRET` / `GTS_CRED_ENC_KEY`; clients open `/authorize`, users submit AK/SK, then use Bearer only (access 1h / refresh 30d).
- **Headers**: `X-GTS-Credentials: {"accessKey":"...","secretKey":"..."}`.

Details: [docs/http-sse.en.md](docs/http-sse.en.md).

</details>

---

## Packages

| Package | Command | Notes |
|---------|---------|--------|
| **[mcp/gangtise_mcp](mcp/gangtise_mcp/)** (recommended) | `gangtise-mcp` | All leaf tools |
| [mcp/gangtise_hub](mcp/gangtise_hub/) | `gangtise-hub-mcp` | Like skill concept, basic 5 domain entry, progressive list/read_ref/call |
| [mcp/gangtise_agent](mcp/gangtise_agent/) | `gangtise-agent-mcp` | Research agents |
| [mcp/gangtise_data](mcp/gangtise_data/) | `gangtise-data-mcp` | Quotes / financials / valuation |
| [mcp/gangtise_file](mcp/gangtise_file/) | `gangtise-file-mcp` | Reports / filings / notes |
| [mcp/gangtise_kb](mcp/gangtise_kb/) | `gangtise-kb-mcp` | Knowledge base |
| [mcp/gangtise_private](mcp/gangtise_private/) | `gangtise-private-mcp` | Drive / meetings / stock pools |

Prefer `gangtise_mcp` unless you need a single domain or the hub router.

---

<details>
<summary><b>Source layout (mcp stdio / api HTTP·SSE / cli)</b></summary>

| Path | Role |
|------|------|
| [`mcp/`](mcp/) | Business scripts + **stdio** MCP (Cursor / local) |
| [`api/`](api/) | **HTTP/SSE** MCP + OAuth/auth (depends on the matching mcp package) |
| [`cli/`](cli/) | CLI (depends on mcp; all-tools command: `gangtise`) |

`gangtise_hub` / `gangtise_mcp` do **not** vendor domain code; they depend on the five domain packages.  
stdio: `cd gangtise-data-mcp/mcp/<pkg> && uv sync && uv run <mcp-cmd>`.  
HTTP: `cd gangtise-data-mcp/api/<pkg> && uv sync && uv run <api-cmd>`.  
Sync: `python3 sync_skills_to_mcp.py` (business → `mcps/mcp/`; auth SSOT → `api/gangtise_agent`).

</details>

<details>
<summary><b>Environment variables</b></summary>

| Variable | Notes |
|----------|--------|
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | Process credentials for stdio |
| `GTS_AUTHORIZATION_PATH` | Optional credentials file |
| `GTS_JWT_SECRET` / `GTS_CRED_ENC_KEY` | Remote OAuth (Fernet key via `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) |
| `GTS_OAUTH_ISSUER` | Public issuer URL behind a reverse proxy |
| `GTS_SAVE_FILE` / `WORK_PATH` | Persist outputs / workspace |
| `GTS_MCP_ROOT` | Gateway root (default `/opt/mcp` in containers) |
| `MCP_ATTACH_MAX_BYTES` / `OBS_*` | Attachment size / optional OBS — see [docs/docker-deploy.en.md](docs/docker-deploy.en.md) |

Without credentials you can still handshake and `tools/list`; tool calls prompt you to get keys on the open platform.

</details>

<details>
<summary><b>Docker one-liner</b></summary>

```bash
cd gangtise-data-mcp
docker build -t gangtise-mcp -f Dockerfile .
docker run -d -p 8000:8000 \
  -e GTS_JWT_SECRET='change-me' \
  -e GTS_CRED_ENC_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  gangtise-mcp
```

See [docs/docker-deploy.en.md](docs/docker-deploy.en.md).

</details>

---

Credentials via the open platform. Docs issues welcome as GitHub Issues. Chinese: [README.md](README.md).
