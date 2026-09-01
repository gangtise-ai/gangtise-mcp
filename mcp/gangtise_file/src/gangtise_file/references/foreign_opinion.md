# 外资机构观点 / 外资独立观点调用指导

## 简介

通过 Open Insight 列表接口分页检索两类海外观点：

| 来源 | OpenAPI 路径 | 说明 |
|------|----------------|------|
| **外资机构观点** | `foreign-opinion/getList` + `getDetail` | 外资券商观点；列表仅返回 `brief` / `briefTranslate`；正文 `content` / `contentTranslate` 由详情接口返回（30 积分/条，单次最多 20 个 ID）。 |
| **外资独立观点** | `independent-opinion/getList` | 海外独立分析师观点；支持下载原文/中文翻译 HTML。 |

主脚本：`scripts/foreign_opinion.py`，用 **`--source`** 切换数据源。境外证券代码格式如 `APP.O`、`UBER.N`（见平台境外股票代码规范）。行业 ID 见 `scripts/get_industries.py`；券商 ID（仅机构观点）见 `scripts/get_institutions.py`；区域代码（仅机构观点）见 `scripts/get_regions.py`。

**权限说明**：试用账号通常为当前时间前溯 **1 个月**、正式账号约 **3 个月** 的历史数据（以接口校验为准）。

## 主脚本参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--source` | 否 | `institution`（默认）— 外资机构观点；`independent` — 外资独立观点。 |
| `-k` / `--keyword` | 否 | 关键词。 |
| `-sd` / `--start-date` | 否 | 开始时间；支持 `YYYY-MM-DD`（自动补 `00:00:00`）或 `yyyy-MM-dd HH:mm:ss`。 |
| `-ed` / `--end-date` | 否 | 结束时间；支持 `YYYY-MM-DD`（自动补 `23:59:59`）或完整时间串。 |
| `-l` / `--limit` | 否 | 返回条数上限；开启 `-d` 且未显式传 `-l` 时默认 5；单页最大 50，脚本内自动分页。 |
| `--securities` | 否 | 证券列表，逗号分隔；经 `security` 解析为代码后传入 `securityList`。 |
| `--industries` | 否 | 行业关键词，逗号分隔，解析为行业 ID 列表。 |
| `--institutions` | 否 | 券商关键词，逗号分隔，解析为 `brokerList`；**仅 `--source institution` 生效**。 |
| `--region-list` | 否 | 区域关键词或代码，逗号分隔，解析为 `regionList`；**仅 `--source institution` 生效**。 |
| `--rating-list` | 否 | 评级：`buy` / `overweight` / `neutral` / `underweight` / `sell`。 |
| `--rating-change-list` | 否 | 评级变动：`upgrade` / `maintain` / `downgrade` / `initiate`。 |
| `--rank-type` | 否 | `1` 综合排序，`2` 时间倒序（默认 `1`）。 |
| `-d` / `--download` | 否 | 检索后自动下载。`institution`：调用 `getDetail` 保存正文；`independent`：下载 HTML。 |
| `-od` / `--output-dir` | 否 | 结果与下载文件保存目录路径，建议绝对路径。 |
| `-dt` / `--download-types` | 否 | **institution**：`txt`（英文原文，默认）、`zh`（中文翻译）。**independent**：`html`（原文）、`html_zh` / `zh`（中文翻译 HTML）。 |

## 返回与类型字段

- **机构观点**：`类型` 为 **外资机构观点**，`类型ID` 为 **`foreignOpinionId`**。列表字段含标题/译文、摘要 brief、发布机构、区域、证券与行业等；完整正文需 `-d` 或 `get_file`。
- **独立观点**：`类型` 为 **外资独立观点**，`类型ID` 为 **`independentOpinionId`**。可配合 `get_file.py` 按 ID 下载 HTML。

## 调用示例

**外资机构观点（关键词 + 区域 + 数量）：**

```bash
python3 scripts/foreign_opinion.py --source institution -k 自动驾驶 --region-list us -l 30
```

**外资机构观点：检索并下载原文与中文翻译：**

```bash
python3 scripts/foreign_opinion.py --source institution -k 腾讯 -l 3 -d -dt txt,zh -od /path/to/out
```

**外资独立观点（关键词 + 时间）：**

```bash
python3 scripts/foreign_opinion.py --source independent -k 肿瘤 -sd 2026-01-01 -ed 2026-05-31 -l 20
```

**独立观点：检索并下载原文与中文翻译：**

```bash
python3 scripts/foreign_opinion.py --source independent -k 半导体 -d -od /path/to/out -dt html,zh
```

**单条下载（与列表返回的类型、ID 一致）：**

```bash
python3 scripts/get_file.py --file-id <foreignOpinionId> --file-type 外资机构观点 -dt txt
python3 scripts/get_file.py --file-id <foreignOpinionId> --file-type 外资机构观点 -dt zh
python3 scripts/get_file.py --file-id <independentOpinionId> --file-type 外资独立观点 -dt html
```

## 与外资研报脚本的差异

- `foreign_report.py` 面向**外资研报 PDF/MD** 下载链路；`foreign_opinion.py` 面向**观点列表 + 正文/HTML**。
- 下载参数：机构观点用 `txt`/`zh`（getDetail）；独立观点用 `html`/`zh`；研报用 `pdf`/`markdown`。
