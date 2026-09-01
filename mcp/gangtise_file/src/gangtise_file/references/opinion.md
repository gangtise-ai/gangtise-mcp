# 首席观点调用指导

## 简介

按关键词、证券、券商、研究方向、首席分析师、概念、投研标签、来源类型等条件检索**首席观点**列表；列表仅返回摘要 `brief`（正文前约 200 字）。完整正文通过详情接口 `chief-opinion/getDetail` 获取（30 积分/条，单次最多 20 个 ID）。主脚本 `scripts/opinion.py` 支持与研报类似的「先列表、再 `-d` 下载正文」流程。

券商枚举：`scripts/get_institutions.py`；研究方向枚举：`scripts/get_industries.py`；首席枚举/筛选：`scripts/get_chiefs.py`（`--name` / `--institution` / `--group`）。

## 主脚本：执行检索

| 参数 | 必填 | 说明 |
|------|------|------|
| `-k` / `--keyword` | 否 | 搜索关键词；可为空。 |
| `-sd` / `--start-date` | 否 | 开始日期，格式 `YYYY-MM-DD`。 |
| `-ed` / `--end-date` | 否 | 结束日期，格式 `YYYY-MM-DD`。 |
| `-l` / `--limit` | 否 | 返回数量上限；开启 `-d` 且未显式传 `-l` 时默认 5。 |
| `--rank-type` | 否 | 排序：`1` 综合排序（默认），`2` 时间倒序。 |
| `--securities` | 否 | 证券列表，逗号分隔；可为证券名称、代码或拼音首字母。 |
| `--industries` | 否 | **研究方向**列表，逗号分隔；传入宏观/策略/固收/金工/海外等名称，脚本映射为 `researchAreaList`（**不使用** `industryList`）。 |
| `--institutions` | 否 | 发布机构（券商），逗号分隔；可选值见枚举脚本。 |
| `--chiefs` | 否 | 首席分析师列表，逗号分隔：支持 `P` 开头 ID 或姓名（模糊匹配）；重名或未命中时请用 `get_chiefs.py` 查 ID。 |
| `--concepts` | 否 | 概念 ID 列表，逗号分隔。暂不支持匹配。 |
| `--llm-tags` | 否 | 投研业务标签，逗号分隔。可选：`strongRcmd`（强烈推荐）、`earningsReview`（业绩点评）、`topBroker`（头部券商）、`newFortune`（新财富团队）；也可传中文。 |
| `--source-types` | 否 | 来源，逗号分隔。可选：`realTime`（实时）、`openSource`（开放来源）；也可传中文。 |
| `-d` / `--download` | 否 | 检索后按观点 ID 调用 `getDetail` 下载正文原文（按条计积分）。 |
| `-od` / `--output-dir` | 否 | 结果与下载文件保存目录，建议绝对路径。 |
| `-dt` / `--download-types` | 否 | 下载格式，逗号分隔：`txt`（默认）/ `md`（正文相同，仅后缀不同）。 |

**无枚举值接口的参数**（如 `keyword`、`chiefs`、`concepts`）：按用户意图直接传入；概念、首席等一般为**业务 ID**，请以平台侧数据为准。

## 枚举值脚本：获取参数可选值

- **券商（发布机构）**：执行 `scripts/get_institutions.py` 获取机构列表。

```bash
python3 scripts/get_institutions.py
```

## 调用示例

**按关键词 + 投研标签 + 来源：**

```bash
python3 scripts/opinion.py -k 半导体 --llm-tags strongRcmd --source-types realTime -l 20
```

**检索并下载正文：**

```bash
python3 scripts/opinion.py -k 安孚科技 -l 5 -d -od /path/to/out -dt txt
```

**按证券（名称或代码）+ 关键词：**

```bash
python3 scripts/opinion.py --securities 贵州茅台 -k 业绩 -l 20
```

**按券商 + 研究方向（宏观/策略/固收/金工/海外等）：**

```bash
python3 scripts/opinion.py --institutions 兴业证券 --industries 宏观,策略 -l 20
```

注意：参数名仍为 `--industries`，但本接口实际发往 `researchAreaList`（研究方向 ID），**不使用** `industryList`；与 `summary.py` 中「行业 + 研究方向混合解析」的用法不同。

**按时间范围 + 排序：**

```bash
python3 scripts/opinion.py -sd 2026-01-01 -ed 2026-05-31 --rank-type 2 -l 30
```

**按首席姓名（或 ID）：**

```bash
python3 scripts/opinion.py --chiefs 郭磊 -l 20
python3 scripts/opinion.py --chiefs P100001117 -l 20
```

**单条下载（与列表返回的类型、ID 一致）：**

```bash
python3 scripts/get_file.py --file-id <chiefOpinionId> --file-type 首席观点 -dt txt
```

## 返回说明

- **成功（列表）**：返回观点列表（观点 ID、标题、摘要 brief、作者与券商、关联证券/行业/概念、标签等）。列表**不含**完整正文。
- **成功（下载）**：对列表中的 `类型ID` 批量调用详情接口，将 `content` 保存为本地 txt/md。
- **失败**：返回错误信息。
