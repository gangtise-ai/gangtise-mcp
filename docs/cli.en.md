# CLI

[简体中文](cli.md) | **English**

Command-line access to the **same tool implementations** as MCP. Prefer the full entry **`gangtise`** ([`cli/gangtise_mcp`](../cli/gangtise_mcp/), depends on [`mcp/gangtise_mcp`](../mcp/gangtise_mcp/)).

Credentials: [open platform](https://open-platform.gangtise.com/) Access Key / Secret Key.

Repo examples in this English doc use GitHub: [`https://github.com/XiaoYan3938/gangtise-data-mcp`](https://github.com/XiaoYan3938/gangtise-data-mcp). Chinese doc defaults to Gitee: [cli.md](cli.md).

---

## Recommended install (`uvx` + `gangtise-mcp`)

Requires [uv](https://docs.astral.sh/uv/). No clone needed:

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_mcp" \
  gangtise --help
```

Then:

```bash
uvx --with "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  --from "git+https://github.com/XiaoYan3938/gangtise-data-mcp#subdirectory=cli/gangtise_mcp" \
  gangtise list
```

---

## Credentials

```bash
uvx ... gangtise configure --access-key <AK> --secret-key <SK>

# or env (overrides local file)
export GTS_ACCESS_KEY=<AK>
export GTS_SECRET_KEY=<SK>
```

| Variable | Notes |
|----------|--------|
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | AK/SK (preferred) |
| `GTS_AUTHORIZATION_PATH` | Custom credentials path |
| `GTS_SAVE_FILE` / `WORK_PATH` | Persist / workspace (some tools) |

---

## Common commands

| Command | Notes |
|---------|--------|
| `gangtise configure` | Save AK/SK |
| `gangtise list` | List tools |
| `gangtise <tool> --help` | Tool help |
| `gangtise <tool> ...` | Invoke (same as MCP) |
| `gangtise uninstall` | Remove local credentials |

Examples:

```bash
gangtise quote --securities 比亚迪
gangtise report -k 新能源 -sd 2026-01-01 -ed 2026-06-30
gangtise pdf_parse --action submit --pdf-path ./sample.pdf
```

---

<details>
<summary><b>Run from source</b></summary>

```bash
cd gangtise-data-mcp/cli/gangtise_mcp
uv sync
uv run gangtise list
```

</details>

<details>
<summary><b>Single-domain CLIs (optional)</b></summary>

See [`cli/`](../cli/) for `gangtise-data`, `gangtise-file`, …, `gangtise-pdf`. Prefer full **`gangtise`** for daily use.

</details>

---

[HTTP / SSE](http-sse.en.md) · [Docker](docker-deploy.en.md) · [Overview](../README.en.md)
