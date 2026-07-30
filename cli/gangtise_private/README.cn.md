# Gangtise Private CLI

**简体中文** | [English](README.md)

云盘、会议、股池、微信消息等私有数据。

入口命令：`gangtise-private`（依赖兄弟包 [`mcp/gangtise_private`](../../mcp/gangtise_private/)，包名 `gangtise-private-mcp`）。  
MCP 服务见 [`mcp/gangtise_private`](../../mcp/gangtise_private/)（命令 `gangtise-private-mcp`）。日常推荐 MCP 用 [`gangtise_mcp`](../../mcp/gangtise_mcp/)。

账号：[开放平台](https://open-platform.gangtise.com/)。

---

## 独立运行

本目录为完整可安装包，可单独：

```bash
cd gangtise-data-mcp/cli/gangtise_private
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise-private --help
```

`uv.sources` 通过相对路径引用 `../../mcp/gangtise_private`，无需整仓 workspace。亦可：

```bash
uvx --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=mcp/gangtise_private" \
  --from "git+https://gitee.com/yanxi3938/gangtise-data-mcp#subdirectory=cli/gangtise_private" \
  gangtise-private list
```

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `gangtise-private configure` | 保存 AK/SK 到 `~/.config/gangtise/authorization` |
| `gangtise-private list` | 列出全部工具子命令 |
| `gangtise-private <tool> --help` | 查看单个工具参数 |
| `gangtise-private <tool> ...` | 调用工具（与 MCP 同源实现） |

```bash
cd gangtise-data-mcp/cli/gangtise_private
uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
uv run gangtise-private list
```

示例：

```bash
  gangtise-private configure --access-key <AK> --secret-key <SK>
  gangtise-private list
```


## 环境变量

| 变量 | 说明 |
|------|------|
| `GTS_ACCESS_KEY` / `GTS_SECRET_KEY` | 凭证（优先级高于本地文件） |
| `GTS_AUTHORIZATION_PATH` | 自定义凭证文件路径 |
| `GTS_SAVE_FILE` / `WORK_PATH` | 是否落盘 / 工作区（部分工具） |

---

English: [README.md](README.md) · 总览: [../../README.cn.md](../../README.cn.md)
