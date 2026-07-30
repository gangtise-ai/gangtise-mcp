# WorkBuddy Connector：gangtise-mcp

**简体中文** | [English](README.en.md)

按 WorkBuddy Connector 第三方开发者对接规范 **v3.0 · 第 13 章（用户自填 Token）** 准备的提交包。

| 项 | 值 |
|----|-----|
| `source` | `gangtise-mcp` |
| 方案 | MCP + Skill（`auth_mode: token`） |
| 传输 | `streamableHttp` |
| 端点 | `https://openapi.gangtise.com/application/open-mcp/` |
| 鉴权 | 表单字段 `gangtiseAccessKey` / `gangtiseSecretKey` → 请求头 `accessKey` / `secretKey` |

后台仍兼容 OAuth / `Authorization` / `X-GTS-Credentials`；本上架包暂不走 OAuth。

## 目录

```
gangtise-mcp/
  connector-meta.json
  mcp.json
  token-schema.json
  icon.svg
  skills/SKILL.md
  LOADTEST_REPORT.md      # 压测报告（WorkBuddy 2.2.5）
  loadtest/               # 压测脚本与曲线图
  README.md / README.en.md
```

## 用户侧体验（上架后）

1. 安装 **Gangtise MCP**。
2. 连接时填写开放平台 Access Key / Secret Key。
3. **信任** 并 **开启** 连接器。

## 提交前自查（Token 模式）

- [ ] `mcp.json` 为 HTTPS `streamableHttp`；头名 `accessKey`/`secretKey`，占位符 `${gangtiseAccessKey}` / `${gangtiseSecretKey}`（勿用易冲突的 `${accessKey}`）
- [ ] `token-schema.json` 字段 `key` 与占位符一致（`gangtiseAccessKey` / `gangtiseSecretKey`）；敏感字段 `type: password`
- [ ] `connector-meta.json`：`auth_mode: "token"`，`minWorkbuddyVersion` ≥ `4.23.0`
- [ ] `description` 说明凭证仅存本机
- [ ] `icon.svg`、`skills/SKILL.md` 已提供
- [ ] 已附带压测报告：[LOADTEST_REPORT.md](LOADTEST_REPORT.md)（脚本见 [loadtest/](loadtest/)）

## 相关链接

- MCP 包：[mcp/gangtise_mcp](../../../mcp/gangtise_mcp/)
- HTTP 鉴权：[docs/http-sse.md](../../../docs/http-sse.md)
- 压测报告：[LOADTEST_REPORT.md](LOADTEST_REPORT.md)
