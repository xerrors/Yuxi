# 决策：.xls 旧版 Excel 使用 pandas + xlrd 解析

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/parser/unified.py

## 问题

Docling 的 `MsExcelDocumentBackend` 仅支持 `.xlsx`（XML 格式），不支持 `.xls`（旧版二进制格式）。原代码将两者路由到同一 Docling 解析器，导致 `.xls` 文件上传后在解析阶段失败。

## 决策

在 `parse_resolved_document` 中将 `.xls` 从 Docling 路径分离，路由到新增的 `_convert_xls_to_markdown`（pandas + xlrd），每个 sheet 输出 Markdown 表格。`.xlsx` 仍走 Docling，行为不变。

读取时不假定首行是表头，保留文本单元格类型，关闭默认缺失值识别和 Markdown 数字推断。所有非空工作表的首行均作为内容输出，Markdown 使用空白表头；只有无数据的工作表被跳过。

## 替代方案

1. 移除 `.xls` 支持（从 `SUPPORTED_FILE_EXTENSIONS` 删除）—— 会拒绝用户的合法文件，与 issue 诉求相反。
2. 要求用户手动转换 — 降低可用性。
3. 迁移到 anydoc — 维护者已注明对图表保留不成熟，等待后续。

## 后果

- 新增依赖 `xlrd>=2.0.1`（轻量、纯 Python，仅用于读取旧格式）。
- `.xls` 不再经过 Docling，嵌入图表不会保留；仅提取单元格数据为 Markdown 表格。
- 无需系统级 LibreOffice。
- 保留文本编号的前导零及 `NA`、`NULL` 等字面值；不还原数字单元格通过 Excel 显示格式呈现的前导零或其他样式。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| .xls 文件能解析为 Markdown | 解析器路由 | `parse_resolved_document` | `pytest test/unit/knowledge/test_parser_facade.py -k xls` | 路由到 Docling 应报错 | Passed |
| .xlsx 走 Docling 不变 | 解析器路由 | `parse_resolved_document` | 同上 fixture `测试表格.xlsx` | 路由到 pandas 应报错 | Passed |
| 无 LibreOffice 也能解析 .xls | 系统依赖 | `_convert_xls_to_markdown` | 本机无 libreoffice 时测试通过 | 缺 xlrd 应报错 | Passed |
| 保留单行、文本编号、NA 字面值与空白单元格 | 首行被当作表头、读取及输出时自动转换 | `_convert_xls_to_markdown` | `pytest test/unit/knowledge/test_parser_facade.py -k preserves_single_row`；真实 fixture `xls-cell-preservation.xls` 含单行、文本与空白、空表三个 sheet | 恢复默认读取参数后单行丢失，文本编号和 NA 改写 | Passed |
