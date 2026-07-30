# Gangtise（全量） CLI

[简体中文](README.md) | **English**

各域全部叶子工具（含 PDF）；与推荐 MCP 包 gangtise_mcp 同源。

Command: `gangtise` (depends on [`api/gangtise_mcp`](../../mcp/gangtise_mcp/), package `gangtise-mcp-api`).  
MCP server: [`mcp/gangtise_mcp`](../../mcp/gangtise_mcp/) (`gangtise-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_mcp
uv sync
uv run gangtise --help
```

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_mcp" \
  gangtise list
```

---

## Examples

```bash
  gangtise configure --access-key <AK> --secret-key <SK>
  gangtise list
  gangtise quote --securities 比亚迪
  gangtise report -k 新能源
```

---

Chinese: [README.md](README.md) · Install guide: [../../docs/cli.en.md](../../docs/cli.en.md) · Overview: [../../README.en.md](../../README.en.md)
