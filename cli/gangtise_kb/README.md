# Gangtise KB CLI

[简体中文](README.cn.md) | **English**

知识库语义检索。

Command: `gangtise-kb` (depends on [`api/gangtise_kb`](../../mcp/gangtise_kb/), package `gangtise-kb-api`).  
MCP server: [`mcp/gangtise_kb`](../../mcp/gangtise_kb/) (`gangtise-kb-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_kb
uv sync
uv run gangtise-kb --help
```

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_kb" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_kb" \
  gangtise-kb list
```

---

## Examples

```bash
  gangtise-kb configure --access-key <AK> --secret-key <SK>
  gangtise-kb list
  gangtise-kb kb -k 示例查询
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
