# Gangtise Hub CLI

[简体中文](README.cn.md) | **English**

渐进披露：五个域入口，再 list / read_ref / call 叶子工具。

Command: `gangtise-hub` (depends on [`api/gangtise_hub`](../../mcp/gangtise_hub/), package `gangtise-hub-api`).  
MCP server: [`mcp/gangtise_hub`](../../mcp/gangtise_hub/) (`gangtise-hub-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_hub
uv sync
uv run gangtise-hub --help
```

```bash
uvx --with "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_hub" \
  --from "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=cli/gangtise_hub" \
  gangtise-hub list
```

---

## Examples

```bash
  gangtise-hub
  gangtise-hub gangtise-data
  gangtise-hub gangtise-data --action read_ref --name quote
  gangtise-hub gangtise-data --action call --name quote --arguments '{"securities":["比亚迪"]}'
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
