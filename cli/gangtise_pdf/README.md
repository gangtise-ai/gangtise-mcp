# Gangtise PDF CLI

[简体中文](README.cn.md) | **English**

High-precision PDF parse (submit / result).

Command: `gangtise-pdf` (depends on [`mcp/gangtise_pdf`](../../mcp/gangtise_pdf/), package `gangtise-pdf-mcp`).  
MCP server: [`mcp/gangtise_pdf`](../../mcp/gangtise_pdf/) (`gangtise-pdf-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_pdf
uv sync
uv run gangtise-pdf --help
```

```bash
uvx --with "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=mcp/gangtise_pdf" \
  --from "git+https://github.com/gangtise-ai/gangtise-mcp#subdirectory=cli/gangtise_pdf" \
  gangtise-pdf list
```

---

## Examples

```bash
  gangtise-pdf configure --access-key <AK> --secret-key <SK>
  gangtise-pdf list
  gangtise-pdf pdf_parse --action submit --pdf-path ./sample.pdf
  gangtise-pdf pdf_parse --action result --task-id <task_id>
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
