# Gangtise Data CLI

[简体中文](README.cn.md) | **English**

行情、财务、估值、资金流向、行业/公司指标等。

Command: `gangtise-data` (depends on [`api/gangtise_data`](../../mcp/gangtise_data/), package `gangtise-data-api`).  
MCP server: [`mcp/gangtise_data`](../../mcp/gangtise_data/) (`gangtise-data-mcp`). Recommended MCP: [`gangtise_mcp`](../../mcp/gangtise_mcp/).

Credentials: [open platform](https://open-platform.gangtise.com/).

---

## Run standalone

```bash
cd gangtise-data-mcp/cli/gangtise_data
uv sync
uv run gangtise-data --help
```

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_data" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_data" \
  gangtise-data list
```

---

## Examples

```bash
  gangtise-data configure --access-key <AK> --secret-key <SK>
  gangtise-data list
  gangtise-data quote --securities 比亚迪
```

---

Chinese: [README.cn.md](README.cn.md) · Overview: [../../README.md](../../README.md)
