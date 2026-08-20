# Gangtise Data CLI

**简体中文** | [English](README.md)

行情、财务、估值、资金流向、行业/公司指标等。

入口命令：`gangtise-data`（依赖兄弟包 [`mcp/gangtise_data`](../../mcp/gangtise_data/)，包名 `gangtise-data-mcp`）。  
MCP 服务见 [`mcp/gangtise_data`](../../mcp/gangtise_data/)（命令 `gangtise-data-mcp`）。日常推荐 MCP 用 [`gangtise_mcp`](../../mcp/gangtise_mcp/)。

账号：[开放平台](https://open-platform.gangtise.com/)。

---

## 独立运行

本目录为完整可安装包，可单独：

```bash
cd gangtise-data-mcp/cli/gangtise_data
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise-data --help
```

`uv.sources` 通过相对路径引用 `../../mcp/gangtise_data`，无需整仓 workspace。亦可：

```bash
uvx --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with "git+https://gitee.com/gangtise/gangtise-mcp#subdirectory=mcp/gangtise_data" \
  --from "git+https://gitee.com/gangtise/gangtise-mcp#subdirectory=cli/gangtise_data" \
  gangtise-data list
```

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `gangtise-data configure` | 保存 AK/SK 到 `~/.config/gangtise/authorization` |
| `gangtise-data list` | 列出全部工具子命令 |
| `gangtise-data <tool> --help` | 查看单个工具参数 |
| `gangtise-data <tool> ...` | 调用工具（与 MCP 同源实现） |

```bash
cd gangtise-data-mcp/cli/gangtise_data
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise-data list
```

示例：

```bash
  gangtise-data configure --access-key <AK> --secret-key <SK>
  gangtise-data list
  gangtise-data quote --securities 比亚迪
```


## 环境变量

| 变量 | 说明 |
|------|------|
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | 凭证（优先级高于本地文件） |
| `GTS_AUTHORIZATION_PATH` | 自定义凭证文件路径 |
| `GTS_SAVE_FILE` / `WORK_PATH` | 是否落盘 / 工作区（部分工具） |

---

English: [README.md](README.md) · 总览: [../../README.cn.md](../../README.cn.md)
