# WorkBuddy Connector: gangtise-mcp

[简体中文](README.md) | **English**

Submission package for WorkBuddy Connector spec **v3.0 · Chapter 11 (OAuth)**.

| Item | Value |
|------|--------|
| `source` | `gangtise-mcp` |
| Mode | MCP + Skill (standard OAuth; no `auth_mode: token`) |
| Transport | `streamableHttp` |
| Endpoint | `https://openapi.gangtise.com/application/open-mcp/` |
| Auth | WorkBuddy OAuth Manager → consent at `/oauth/authorize` (AK/SK) |

The same backend still accepts raw `Authorization` and `X-GTS-Credentials`; this marketplace package exposes OAuth only.

## Layout

```
gangtise-mcp/
  connector-meta.json
  mcp.json
  icon.svg
  skills/SKILL.md
  README.md / README.en.md
```

## User flow

1. Install **Gangtise MCP**.
2. On connect, WorkBuddy opens the browser consent page for open-platform AK/SK.
3. **Trust** and **enable** the connector.

## Pre-submit checklist (OAuth)

- [ ] Single HTTPS server in `mcp.json`; no static token headers
- [ ] `connector-meta.json` does **not** set `auth_mode: "token"`
- [ ] Server implements well-known / register / authorize / token; `GTS_JWT_SECRET` + `GTS_OAUTH_ISSUER` set
- [ ] Unauthenticated MCP calls return 401 with `WWW-Authenticate` resource_metadata
- [ ] `icon.svg` and `skills/SKILL.md` present

## Links

- MCP package: [mcp/gangtise_mcp](../../../mcp/gangtise_mcp/)
- HTTP / OAuth: [docs/http-sse.en.md](../../../docs/http-sse.en.md)
