# WorkBuddy Connector：gangtise-mcp

**简体中文** | [English](README.en.md)

按 WorkBuddy Connector 第三方开发者对接规范 **v3.0 · 第 11 章（OAuth）** 准备的提交包。

| 项 | 值 |
|----|-----|
| `source` | `gangtise-mcp` |
| 方案 | MCP + Skill（标准 OAuth，无 `auth_mode: token`） |
| 传输 | `streamableHttp` |
| 端点 | `https://openapi.gangtise.com/application/open-mcp/` |
| 鉴权 | WorkBuddy OAuth Manager → 本服务 `/oauth/authorize` 同意页（填 AK/SK） |

后台同一服务仍兼容：直传 `Authorization`、`X-GTS-Credentials`；上架包仅暴露 OAuth。

## 目录

```
gangtise-mcp/
  connector-meta.json
  mcp.json
  icon.svg
  skills/SKILL.md
  README.md / README.en.md
```

## 用户侧体验（上架后）

1. 安装 **Gangtise MCP**。
2. 连接时 WorkBuddy 打开浏览器，在同意页填写开放平台 AK/SK。
3. 授权成功后 **信任** 并 **开启** 连接器。

## 提交前自查（OAuth）

- [ ] `mcp.json` 仅一个 Server，HTTPS `url`，无静态 Token 头
- [ ] `connector-meta.json` **未**设置 `auth_mode: "token"`
- [ ] 服务已实现 well-known / register / authorize / token，并配置 `GTS_JWT_SECRET`、`GTS_OAUTH_ISSUER`
- [ ] 未认证 MCP 请求返回 401 + `WWW-Authenticate` resource_metadata
- [ ] `icon.svg`、`skills/SKILL.md` 已提供

## 相关链接

- MCP 包：[mcp/gangtise_mcp](../../../mcp/gangtise_mcp/)
- HTTP / OAuth：[docs/http-sse.md](../../../docs/http-sse.md)
