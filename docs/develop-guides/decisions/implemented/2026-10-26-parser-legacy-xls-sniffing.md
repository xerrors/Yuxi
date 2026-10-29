# 解析器 .xls 按文件头嗅探分派

状态：implemented
类型：bug-fix
Owner：backend/yuxi/infrastructure/document_parsing/parser.py

## 问题

解析器把 `.xls` 直接交给 xlrd（仅支持旧版 BIFF 二进制）。WPS 与网页系统导出的"伪 `.xls`"实际是 xlsx 改名（zip）、XML Spreadsheet 2003 或 HTML 表格另存，读取文件头即抛 `XLRDError: Expected BOF record`、`BadZipFile` 等底层英文异常，文件落为解析失败，用户无法从报错判断该怎么做。同时 `_OFFICE_BACKENDS` 把 `.xls` 映射到 Docling MsExcelDocumentBackend（基于 openpyxl，只读得动 xlsx），与"Docling 不支持该格式"的注释矛盾，是无人调用的误导性死条目。

## 决策

### 实现方案

`.xls` 入口改为 `_convert_legacy_excel_to_markdown()` 按真实文件头分派：OLE 魔数走 xlrd（保持既有渲染口径），`PK\x03\x04` 走 openpyxl，markup 开头按命名空间区分 XML Spreadsheet 2003（`ss:Index`/`ss:MergeAcross` 展开为网格）与 HTML（`pd.read_html` 保留 `<th>` 表头）；文本类内容复用 `decode_user_text` 处理非 UTF-8 声明。全部工作表为空或文件头不可识别时显式抛中文 `ValueError`（提示另存为 `.xlsx`）。删除 `_OFFICE_BACKENDS` 中的 `.xls` 死映射。xlsx 真文件仍走 Docling 主路径，本分派只处理以 `.xls` 命名的文件。

## 替代方案

- 引入 `python-calamine` 统一替换 xlrd：拒绝。新增原生依赖需要镜像重建，且当前失败主因是扩展名与真实格式不符，不是 xlrd 对合法 BIFF 的兼容缺陷。
- 上传时按扩展名拒绝"伪 xls"：拒绝。用户无从得知真实格式，且历史文件仍无法解析。

## 后果

- 同一 `.xls` 扩展名下四种真实格式全部有确定结局：解析成功或中文可读错误；error_message 从底层异常变为可操作提示。
- 全空工作簿由"静默解析成功、零内容索引"变为显式失败；依赖空 .xls 解析成功的流程（如占位模板）会开始看到失败记录。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 合法 BIFF .xls 解析不回归 | 嗅探分派误伤真 xls | `_convert_legacy_excel_to_markdown` | `test/unit/knowledge/test_parser_legacy_excel.py::test_biff_xls_still_parses`（真实 fixture） | 分派表把 OLE 魔数错接其他引擎时该用例失败 | Passed |
| xlsx 改名 .xls 可解析 | xlrd 直读抛 `BadZipFile` | 同上（zip 签名→openpyxl） | `::test_renamed_xlsx_falls_back_to_openpyxl` | 同用例先断言 `engine="xlrd"` 必抛异常 | Passed |
| XML Spreadsheet 2003 可解析且保留列位 | xlrd 抛 `Expected BOF record` | 同上（SpreadsheetML 分支） | `::test_spreadsheetml_parses_with_index_and_merge` | 同上 xlrd 负控 | Passed |
| HTML 表格冒充可解析 | 底层异常外泄 | 同上（HTML 分支） | `::test_html_table_masquerading_as_xls` | 同上 xlrd 负控 | Passed |
| 空工作簿/不可识别内容给中文错误 | 静默产出空 parsed markdown | 同上 | `::test_spreadsheetml_without_content_fails_readable`、`::test_unrecognizable_content_fails_readable` | 旧实现返回空串或抛英文异常 | Passed |
| 解析入口对 .xls 走嗅探分派 | 入口仍直读 xlrd | `parser.py` 分派 | `::test_parse_routes_xls_through_header_sniffing`；`pytest test/unit/knowledge` → 388 passed | 不适用 | Passed |

未验证范围：真实 WPS 产出的加密集合或含样式扩展记录的 BIFF8 样本；线上解析失败记录需部署后回读确认。
