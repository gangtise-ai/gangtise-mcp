# Gangtise File CLI

[简体中文](README.cn.md) | **English**

研报、公告、纪要、观点、日历与文件检索等。

Command: `gangtise-file` (depends on [`api/gangtise_file`](../../mcp/gangtise_file/), package `gangtise-file-api`).  
MCP server: [`mcp/gangtise_file`](../../mcp/gangtise_file/) (`gangtise-file-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_file
uv sync
uv run gangtise-file --help
```

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_file" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_file" \
  gangtise-file list
```

---

## Examples

```bash
  gangtise-file configure --access-key <AK> --secret-key <SK>
  gangtise-file list
  gangtise-file report -k 比亚迪
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
