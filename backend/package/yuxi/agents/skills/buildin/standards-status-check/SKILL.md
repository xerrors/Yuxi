---
name: standards-status-check
slug: standards-status-check
description: "检查三体系标准条款库对应的 GB/T 标准是否仍为现行版本，并校验知识库/文件名称、结构化条款来源指纹是否匹配。当用户询问质量、环境、职业健康安全三体系标准库是否最新、是否现行、是否有修订计划，或要求核对知识库/文件命名规范、结构化条款是否同步时使用此技能。"
---

# 三体系标准状态检查

用于检查三体系标准条款库对应的国家标准是否仍为现行版本，并核对本地知识库或文件名是否包含正确的标准号和官方名称；如果用户维护了结构化条款 Markdown，还应检查结构化文件 frontmatter 中的 `source_sha256` 是否仍匹配当前原标准文件。

## 覆盖标准

- `GB/T 45001-2020`：职业健康安全管理体系 要求及使用指南
- `GB/T 24001-2016`：环境管理体系 要求及使用指南
- `GB/T 19001-2016`：质量管理体系 要求

## 操作流程

1. 当用户要检查三体系标准是否最新、是否现行、是否有修订计划时，使用本技能。
2. 通过 terminal 进入技能目录：`cd /home/gem/skills/standards-status-check`
3. 如果用户提供了知识库名或文件名，逐个传给脚本：

```bash
python scripts/check_standards_status.py \
  --name "GB/T 45001-2020 职业健康安全管理体系 要求及使用指南" \
  --name "GB/T 24001-2016 环境管理体系 要求及使用指南.docx" \
  --name "GB/T 19001-2016 质量管理体系 要求.docx"
```

4. 如果用户只要求检查官方状态，不需要本地命名校验，直接运行：

```bash
python scripts/check_standards_status.py
```

5. 如果用户提供了结构化条款文件或目录，传入 `--structured-file` 或 `--structured-dir`；如果原标准文件目录与结构化文件不在同一目录，传入 `--source-root`：

```bash
python scripts/check_standards_status.py \
  --structured-dir "/path/to/02_结构化条款" \
  --source-root "/path/to/01_标准原文"
```

结构化条款文件应在 YAML frontmatter 中包含：

```yaml
standard_no: GB/T 24001-2016
source_file: GBT 24001-2016 环境管理体系 要求及使用指南.docx
source_kb_path: 01_标准原文/GBT 24001-2016 环境管理体系 要求及使用指南.docx
source_sha256: 原标准文件SHA256
```

`source_kb_path` 使用知识库内逻辑路径，不要写本机绝对路径；`source_size_bytes`、`source_last_modified` 可作为辅助字段；检查时以 `source_sha256` 为核心依据。

6. 根据脚本输出生成结论，优先说明：
   - 是否仍为 `现行`
   - 是否存在修订计划
   - 本地知识库/文件名是否能通过标准号匹配
   - 本地名称是否与官方名称一致或存在格式差异
   - 结构化条款的原件 Hash 是否匹配
   - 结构化条款标题数量与 `### 条款原文` 数量是否一致

7. 如果结构化条款已过期，且用户要求生成更新草稿，使用 `--rebuild-draft`。脚本只会把草稿写入 outputs，不会覆盖正式结构化文件：

```bash
python scripts/check_standards_status.py \
  --structured-dir "/path/to/02_结构化条款" \
  --source-root "/path/to/01_标准原文" \
  --rebuild-draft
```

在 Agent 沙盒中默认输出到 `/home/gem/user-data/outputs`。本地调试可用 `--output-dir` 指定目录。仅人工复核时可使用 `--force-rebuild-draft` 强制生成草稿。

草稿生成规则：

- 以现有结构化 Markdown 的条款号层级为模板，不直接按 docx 全量拆分术语或附录。
- 条款号相同的内容会保留旧文件中的 `关键词`、`条款关联`、`标准含义`、`审核意图`。
- 条款原文变化时标记 `复核状态：needs_review`。
- 新生成文件带 `draft_status: pending_review`，需要人工确认后再通过知识库页面或现有导入接口替换正式文件。

## 输出要求

- 先给结论，再列证据。
- 对 `GB/T 24001-2016`，如果官方页面显示修订计划，应明确提示“当前仍现行，但存在修订计划，正式新版发布前仍以官方平台状态为准”。
- 不要用知识库名称是否完全一致作为唯一判断依据；标准号是主键，官方名称是辅助校验项。
- 不要默认结构化条款会随原文自动更新；只要 `source_sha256` 不匹配，就应提示需要复核或重构结构化条款。
- 不要直接覆盖正式结构化文件或自动替换知识库文档；本技能只生成草稿，替换与重新入库必须由用户确认后执行。
- 如果脚本提示官方平台无法访问，应明确说明“本次未能完成实时官方状态核验”，不要把内置基线当作实时结论。
- 不要输出标准正文原文。

## 命名建议

知识库或文件名建议采用：

- `GB/T 45001-2020 职业健康安全管理体系 要求及使用指南`
- `GB/T 24001-2016 环境管理体系 要求及使用指南`
- `GB/T 19001-2016 质量管理体系 要求`

允许 `GBT`、`GB/T45001-2020`、全角破折号等常见写法，脚本会按标准号归一化匹配。
