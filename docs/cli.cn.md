# CLI

**简体中文** | [English](cli.md)

命令行调用与 MCP **同源工具实现**。日常推荐使用全量入口 **`gangtise`**（对应 [`cli/gangtise_mcp`](../cli/gangtise_mcp/)，依赖 [`mcp/gangtise_mcp`](../mcp/gangtise_mcp/)）。

账号：[开放平台](https://open-platform.gangtise.com/) 申请 Access Key / Secret Key。

仓库示例（中文文档默认 Gitee）：[`https://gitee.com/yanxi3938/gangtise-data-mcp`](https://gitee.com/yanxi3938/gangtise-data-mcp)。英文示例见 [cli.md](cli.md)（GitHub）。

---

## 推荐安装（`uvx` + `gangtise-mcp`）

需已安装 [uv](https://docs.astral.sh/uv/)。无需克隆仓库即可运行：

```bash
uvx --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  --from "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=cli/gangtise_mcp" \
  gangtise --help
```

首次会拉取依赖；之后可直接：

```bash
uvx --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_mcp" \
  --from "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=cli/gangtise_mcp" \
  gangtise list
```

可将上述 `uvx ... gangtise` 做成 shell 别名，便于日常调用。

---

## 配置凭证

```bash
# 写入 ~/.config/gangtise/authorization（推荐）
uvx ... gangtise configure --access-key <AK> --secret-key <SK>

# 或环境变量（优先级高于本地文件）
export GTS_ACCESS_KEY=<AK>
export GTS_SECRET_KEY=<SK>
```

| 变量 | 说明 |
|------|------|
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | AK/SK（优先） |
| `GTS_AUTHORIZATION_PATH` | 自定义凭证文件路径 |
| `GTS_SAVE_FILE` / `WORK_PATH` | 是否落盘 / 工作区（部分工具） |

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `gangtise configure` | 保存 AK/SK |
| `gangtise list` | 列出全部工具子命令 |
| `gangtise <tool> --help` | 查看单个工具参数 |
| `gangtise <tool> ...` | 调用工具（与 MCP 同源） |
| `gangtise uninstall` | 删除本地凭证文件 |

参数名多为 kebab-case（如 `--start-date`），并支持常用短选项（如 `-k`、`-sd`、`-ed`）。

示例：

```bash
gangtise quote --securities 比亚迪
gangtise report -k 新能源 -sd 2026-01-01 -ed 2026-06-30
gangtise pdf_parse --action submit --pdf-path ./sample.pdf
```

---

<details>
<summary><b>从源码本地运行</b></summary>

```bash
cd gangtise-data-mcp/cli/gangtise_mcp
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise list
```

`uv.sources` 通过相对路径引用 `../../mcp/gangtise_mcp`，无需整仓 workspace。

</details>

<details>
<summary><b>单域 CLI（可选）</b></summary>

仅需某一域时可安装对应包（命令分别为 `gangtise-data` / `gangtise-file` / … / `gangtise-pdf`），见 [`cli/`](../cli/) 下各目录 README。日常更推荐全量 **`gangtise`**。

</details>

---

[HTTP / SSE](http-sse.cn.md) · [Docker 部署](docker-deploy.cn.md) · [总览](../README.cn.md)
