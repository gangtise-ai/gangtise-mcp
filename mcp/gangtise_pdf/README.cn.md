# gangtise-pdf-mcp

高精度 PDF 解析 MCP（OpenAPI `file-parse`）：**一个工具两个 action**。

| action | 作用 |
|--------|------|
| `submit` | 上传 PDF，返回 `taskId` 与预估耗时（约 **5s 启动 + 1s/页**） |
| `result` | 按 `taskId` 下载并解压 Markdown + 图片；未完成可稍后重试 |

积分：提交成功后 **0.8 积分/页**；`result` 不另计费。勿在结果未就绪时重复 `submit`。

## 本地 stdio

```bash
cd mcp/gangtise_pdf
uv run gangtise-pdf-mcp
```

## 工具参数

见 `src/gangtise_pdf/references/pdf_parse.yaml`。

## CLI

命令行入口见 [`cli/gangtise_pdf`](../../cli/gangtise_pdf/)（`gangtise-pdf`）；全量 CLI 亦含 `pdf_parse`（[`cli/gangtise_mcp`](../../cli/gangtise_mcp/)）。
