# Gangtise Private CLI

[简体中文](README.cn.md) | **English**

云盘、会议、股池、微信消息等私有数据。

Command: `gangtise-private` (depends on [`api/gangtise_private`](../../mcp/gangtise_private/), package `gangtise-private-api`).  
MCP server: [`mcp/gangtise_private`](../../mcp/gangtise_private/) (`gangtise-private-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_private
uv sync
uv run gangtise-private --help
```

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_private" \
  gangtise-private list
```

---

## Examples

```bash
  gangtise-private configure --access-key <AK> --secret-key <SK>
  gangtise-private list
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
