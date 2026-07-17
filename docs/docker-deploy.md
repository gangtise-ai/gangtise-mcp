# Docker 部署

**简体中文** | [English](docker-deploy.en.md)

仅提供 **整合镜像**（`mcps/Dockerfile`）：`api/*` + `mcp/*`，默认百炼部署（`MCP_LAYOUT=unified`、`MCP_TRANSPORT=http`、Authorization 透传、参数扁平化）。客户端连 **`/mcp`**。协议与鉴权见 [http-sse.md](http-sse.md)。入口：[`mcp/gangtise_mcp/entrypoint.sh`](../mcp/gangtise_mcp/entrypoint.sh)。

构建时分两类「源」：

| 类型 | 用途 | 国内怎么用 |
|------|------|------------|
| **基础镜像** `BASE_IMAGE` | 拉取官方 `python:3.11.9`（Docker Hub） | 默认即可。加速请在 **Docker Desktop / daemon** 配置 `registry-mirrors` |
| **pip 索引** | 安装 Python 依赖 | 文档示例统一用 **清华源** |

---

<details>
<summary><b>构建与运行</b></summary>

```bash
cd gangtise-data-mcp   # 即仓库中的 mcps/ 目录

docker build -t gangtise-mcp -f Dockerfile \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
  .

docker run -d --name gangtise-mcp -p 8000:8000 gangtise-mcp

curl -sS http://127.0.0.1:8000/health
```

客户端连接 `http://127.0.0.1:8000/mcp`，请求头携带 `Authorization: Bearer <token>`（原样透传下游数据接口）。

</details>

<details>
<summary><b>常用环境变量</b></summary>

| 变量 | 默认 | 说明 |
|------|------|------|
| `MCP_TRANSPORT` | `http` | `http` / `sse` / `both` |
| `MCP_LAYOUT` | `unified` | `unified`（单进程全量叶子）/ `gateway` |
| `MCP_PACKAGE` | `domains` | `domains` / `all` / 单域 slug |
| `MCP_REQUIRE_AUTH` | `true` | `/mcp` 缺少 `Authorization` 时返回 401 |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | 构建期扫描生成的 tool→URL 依赖图 |
| `GTS_MCP_ROOT` | `/opt/mcp` | 下含 `api/` 与 `mcp/` |
| `MCP_ATTACH_MAX_BYTES` | `33554432` | 嵌入附件上限 |
| `OBS_*` | 空 | 超大附件外置（可选） |

工具可见性：构建期扫描各工具对 `*_URL` 的依赖；运行时 `get_white_list()`（当前 stub 返回全量 URL）按白名单过滤 `tools/list` 与 `call`。无 URL 依赖的工具始终放行；白名单为空（用户被 ban）时，有 URL 依赖的工具全部隐藏。

百炼：回传 `X-DashScope-Request-ID`；工具参数 schema 扁平化（`array`/`object` → `string`）。本分支**无** SPI / AK·SK / OAuth。

未配置 OBS 时超出上限的附件会被舍弃；已配置则上传并返回约 1 天链接。构建安装 OBS SDK：`--build-arg INSTALL_OBS=1`。

</details>

---

[HTTP / SSE](http-sse.md) · [总览](../README.md)
