# Gangtise Hub CLI

**简体中文** | [English](README.md)

渐进披露：五个域入口，再 list / read_ref / call 叶子工具。

入口命令：`gangtise-hub`（依赖兄弟包 [`mcp/gangtise_hub`](../../mcp/gangtise_hub/)，包名 `gangtise-hub-mcp`）。  
MCP 服务见 [`mcp/gangtise_hub`](../../mcp/gangtise_hub/)（命令 `gangtise-hub-mcp`）。日常推荐 MCP 用 [`gangtise_mcp`](../../mcp/gangtise_mcp/)。

账号：[开放平台](https://open-platform.gangtise.com/)。

---

## 独立运行

本目录为完整可安装包，可单独：

```bash
cd gangtise-data-mcp/cli/gangtise_hub
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise-hub --help
```

`uv.sources` 通过相对路径引用 `../../mcp/gangtise_hub`，无需整仓 workspace。亦可：

```bash
uvx --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with "git+https://gitee.com/gangtise/gangtise-mcp#subdirectory=mcp/gangtise_hub" \
  --from "git+https://gitee.com/gangtise/gangtise-mcp#subdirectory=cli/gangtise_hub" \
  gangtise-hub list
```

---

## 用法

```bash
cd gangtise-data-mcp/cli/gangtise_hub
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise-hub                # 列出五域
uv run gangtise-hub gangtise-data  # 域说明 / 叶子列表
```

```bash
  gangtise-hub
  gangtise-hub gangtise-data
  gangtise-hub gangtise-data --action read_ref --name quote
  gangtise-hub gangtise-data --action call --name quote --arguments '{"securities":["比亚迪"]}'
```

`call` 需要已配置 AK/SK（`GTS_ACCESS_KEY`/`GTS_SECRET_KEY` 或凭证文件）。


## 环境变量

| 变量 | 说明 |
|------|------|
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | 凭证（优先级高于本地文件） |
| `GTS_AUTHORIZATION_PATH` | 自定义凭证文件路径 |
| `GTS_SAVE_FILE` / `WORK_PATH` | 是否落盘 / 工作区（部分工具） |

---

English: [README.md](README.md) · 总览: [../../README.cn.md](../../README.cn.md)
