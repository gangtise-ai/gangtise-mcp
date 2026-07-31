# Gangtise Agent CLI

[简体中文](README.cn.md) | **English**

一页纸、投资逻辑、同业对比等研报 Agent。

Command: `gangtise-agent` (depends on [`api/gangtise_agent`](../../mcp/gangtise_agent/), package `gangtise-agent-api`).  
MCP server: [`mcp/gangtise_agent`](../../mcp/gangtise_agent/) (`gangtise-agent-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_agent
uv sync
uv run gangtise-agent --help
```

```bash
uvx --with "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_agent" \
  --from "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=cli/gangtise_agent" \
  gangtise-agent list
```

---

## Examples

```bash
  gangtise-agent configure --access-key <AK> --secret-key <SK>
  gangtise-agent list
  gangtise-agent stock-one-pager --securities 比亚迪
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
