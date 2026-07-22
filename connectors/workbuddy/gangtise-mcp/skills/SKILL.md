---
name: gangtise-mcp
description: Use Gangtise MCP tools for financial quotes, research reports, knowledge base, stock pools. WorkBuddy connects via OAuth consent (open-platform AK/SK).
version: 1.2.0
author: Gangtise
---

# Gangtise MCP

本 Skill 指导 AI 使用 **Gangtise MCP**。参数以 MCP `list_tools` schema 为准。

## 连接与凭证（WorkBuddy）

- **端点**：`https://openapi.gangtise.com/application/open-mcp/`
- **鉴权**：OAuth 2.1 + PKCE。用户在浏览器同意页填写开放平台 AK/SK；WorkBuddy 持有 access/refresh，请求时带 Bearer。
- 凭证获取：[开放平台](https://open-platform.gangtise.com/)
- 若 401 / 授权过期：提示用户重新连接连接器完成授权。

后台亦支持直传业务 `Authorization` 或 `X-GTS-Credentials`（非本 Connector 表单流程）。

## 能力总览（五域）

| 域 | 典型能力 |
|----|----------|
| **data** | 行情、财务、宏观 |
| **agent** | 研报、一页纸等 |
| **file** | 文件上传/解析 |
| **kb** | 知识库 |
| **private** | 股票池等 |

## 使用原则

1. 先读 tool schema，勿臆造参数。
2. 标的代码与时间范围按工具说明填写；不确定时先向用户确认。
3. 无权限的工具不会出现在 `list_tools` 中，勿强行调用。
4. 超时可重试一次；业务错误说明原因后调整参数。

## English

- Transport: remote streamable HTTP.
- Auth (WorkBuddy): OAuth consent with open-platform AK/SK; client stores tokens.
- Prefer MCP tool schemas for parameters.
