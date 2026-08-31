# Docker 部署

**简体中文** | [English](docker-deploy.md)

仅提供 **整合镜像**（`mcps/Dockerfile`）：`api/*` + `mcp/*`，默认 HTTP 部署（`MCP_LAYOUT=unified`、`MCP_TRANSPORT=http`、Authorization 透传、参数扁平化）。客户端默认连 **`/`**（`MCP_PATH`）；线上网关可再加前缀如 `/application/mcp`。协议与鉴权见 [http-sse.cn.md](http-sse.cn.md)。入口：[`mcp/gangtise_mcp/entrypoint.sh`](../mcp/gangtise_mcp/entrypoint.sh)。

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

客户端连接 `http://127.0.0.1:8000/`，请求头携带 `Authorization: Bearer <token>`（原样透传下游数据接口）。

</details>

<details>
<summary><b>常用环境变量</b></summary>

| 变量 | 默认 | 说明 |
|------|------|------|
| `GANGTISE_AUTH_DOMAIN` | `https://openapi.gangtise.com/application/auth` | loginV2 基址；实际请求 `{GANGTISE_AUTH_DOMAIN}/oauth/open/loginV2` |
| `MCP_TRANSPORT` | `http` | `http` / `sse` / `both` |
| `MCP_LAYOUT` | `unified` | `unified`（单进程全量叶子）/ `gateway` |
| `MCP_PACKAGE` | `domains` | `domains` / `all` / 单域 slug |
| `MCP_PATH` | `/` | 服务内 MCP 挂载路径；空或不配=根路径 |
| `SUBPATHS` | 空 | 来源方子路径，逗号分隔；如 `doubao,ali,comate` 时 `/open-mcp/doubao/` 与 `/open-mcp/` 等价，访问日志带 `source` 字段 |
| `MCP_REQUIRE_AUTH` | `true` | MCP 路径缺少 `Authorization` 时返回 401 |
| `MCP_TOOL_BLACKLIST` | 空 | 逗号分隔工具名黑名单；命中则 `tools/list` 不展示且 `call` 拒绝 |
| `TOOL_URL_DEPS_PATH` | `/opt/mcp/tool_url_deps.json` | 构建期工具→API path 依赖图 |
| `MCP_API_GETLIST_PATH` | `/api/getList` | 用户 API 白名单（拼在 `GANGTISE_DATA_DOMAIN` 后） |
| `MCP_WHITELIST_CACHE_SEC` | `300` | getList 结果缓存秒数 |
| `MCP_WHITELIST_STRICT` | `false` | `true` 时 getList 失败返回空白名单；默认失败回退全量 |
| `GTS_MCP_ROOT` | `/opt/mcp` | 下含 `api/` 与 `mcp/` |
| `MCP_ATTACH_MAX_BYTES` | `33554432` | 嵌入附件上限；超出则改走 OBS（若已配置） |
| `MCP_ATTACH_OBS_ALWAYS` | `false` | `true` 时任意附件都上传 OBS，正文只返回下载链接（不嵌入 blob；适合 WorkBuddy） |
| `OBS_*` | 空 | OBS 外置：`OBS_ACCESS_KEY` / `SECRET_KEY` / `ENDPOINT` / `BUCKET` / `PATH` |
| `OBS_EXPIRE_DAYS` | `1` | OBS 对象存活天数，到期自动删除 |

工具可见性：`MCP_TOOL_BLACKLIST` 优先绝对屏蔽；其余按构建期 path 依赖 + 运行时 `get_white_list()`（请求 `GANGTISE_DATA_DOMAIN` + `/api/getList`）过滤 `tools/list` 与 `call`。无 path 依赖的工具（且不在黑名单）放行；白名单为空（用户被 ban / 严格模式下 getList 失败）时，有 path 依赖的工具全部隐藏。

部署：回传 `X-DashScope-Request-ID`；工具参数 schema 扁平化（`array`/`object` → `string`）。本分支**无** SPI / AK·SK / OAuth。

未配置 OBS 时超出上限的附件会被舍弃；已配置则上传并返回约 `OBS_EXPIRE_DAYS` 天（默认 1 天）有效链接。WorkBuddy 等不解析 MCP EmbeddedResource 的客户端请设 `MCP_ATTACH_OBS_ALWAYS=true`，使任意大小文件均上传 OBS 并在正文给出链接。构建安装 OBS SDK：`--build-arg INSTALL_OBS=1`。

</details>

---

[HTTP / SSE](http-sse.cn.md) · [总览](../README.cn.md)
