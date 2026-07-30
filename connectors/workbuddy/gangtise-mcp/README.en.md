# WorkBuddy Connector: gangtise-mcp

[简体中文](README.md) | **English**

Submission package for WorkBuddy Connector spec **v3.0 · Chapter 13 (user-supplied token)**.

| Item | Value |
|------|--------|
| `source` | `gangtise-mcp` |
| Mode | MCP + Skill (`auth_mode: token`) |
| Transport | `streamableHttp` |
| Endpoint | `https://openapi.gangtise.com/application/open-mcp/` |
| Auth | Form fields `gangtiseAccessKey` / `gangtiseSecretKey` → headers `accessKey` / `secretKey` |

The backend still accepts OAuth / `Authorization` / `X-GTS-Credentials`; this marketplace package does not use OAuth for now.

## Layout

```
gangtise-mcp/
  connector-meta.json
  mcp.json
  token-schema.json
  icon.svg
  skills/SKILL.md
  LOADTEST_REPORT.md      # load-test report (WorkBuddy 2.2.5)
  loadtest/               # scripts + charts
  README.md / README.en.md
```

## User flow

1. Install **Gangtise MCP**.
2. Enter open-platform Access Key / Secret Key.
3. **Trust** and **enable** the connector.

## Pre-submit checklist (token mode)

- [ ] HTTPS `streamableHttp` in `mcp.json`; header names `accessKey`/`secretKey`, placeholders `${gangtiseAccessKey}` / `${gangtiseSecretKey}` (avoid generic `${accessKey}`)
- [ ] `token-schema.json` keys match placeholders (`gangtiseAccessKey` / `gangtiseSecretKey`); secrets use `type: password`
- [ ] `connector-meta.json`: `auth_mode: "token"`, `minWorkbuddyVersion` ≥ `4.23.0`
- [ ] Description states credentials stay on-device
- [ ] `icon.svg` and `skills/SKILL.md` present
- [ ] Load-test report attached: [LOADTEST_REPORT.md](LOADTEST_REPORT.md) (see [loadtest/](loadtest/))

## Links

- MCP package: [mcp/gangtise_mcp](../../../mcp/gangtise_mcp/)
- HTTP auth: [docs/http-sse.en.md](../../../docs/http-sse.en.md)
- Load-test report: [LOADTEST_REPORT.md](LOADTEST_REPORT.md)
