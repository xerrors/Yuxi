# Semantic 分块保留 HTML 合并表格的行语义

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/chunking/ragflow_like/utils/table_utils.py

## 问题

HTML 表格的横向合并单元格展开后被重复输出；跨越多个逻辑字段的说明被误当作多个字段值。表格整体进入长度切分时，行所属类别与条件也可能在切分后分离。解析引擎已经提供的合并关系不应在分块阶段丢失。

## 决策

### 实现方案

表格转换器保留单元格身份：横向合并只消费一次，纵向合并的类别值随每个数据行继承；同一逻辑表头下的子单元格合并成一个字段，跨逻辑字段的内容保留为独立正文，不猜测字段归属。

Semantic 的 HTML 入口按完整表格行输出独立块，并保留同一 HTML 块中的其他表格与非表格正文。表格行作为原子单元，超长行允许超过长度目标以保留条件关系。来源仍使用原 HTML token 的行区间，不伪造更精确的位置。

多层表头只取显式 `thead` 的列标题路径；缺少 `thead` 时以第一行为表头，表体中的 `th` 仍作为数据。表格标题随行保留。无法解析为正整数的跨度和嵌套表格保留 HTML，不生成不可信的字段关系。该变化不需要新增配置、解析引擎或检索机制；已有文件重新分块后获得新结果，不自动重写旧数据。

## 替代方案

按输出字符串去重无法区分真实独立单元格与横向合并，也不能修复跨字段说明。直接保留全部 HTML 可以避免错误键值关系，但无法提供每行独立完整的检索记录。更换 OCR 引擎不能修复本地转换器对正确合并结构的错误处理。

## 后果

表格行单独成块会增加块数量，超长行可能超过长度目标；不宣称它保证更高召回率。原 HTML 为单行时，每个行块仍只能定位到该原文行。识别阶段已经缺失或识别错的内容不由本修复推断补齐。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 合并字段只输出一次且类别随行继承 | colspan 重复、rowspan 上文丢失 | table_utils.py | `backend/test/unit/knowledge/chunking/test_semantic_tables.py` 与实际原文重放 | 合并表头、嵌套学历条件与跨行类别 | Passed |
| 说明不被错误归到学历或资历 | 一个 colspan 被广播到不同逻辑表头 | table_utils.py | 同上，使用人工明确的完整预期文本 | 跨字段说明与重复文字的独立列 | Passed |
| 每个检索块包含完整行，正文和来源不丢失 | 超长行切断、只消费第一个 table、吞掉贴表正文 | semantic.py | 真实分块入口与来源投影 unit | 小长度目标、多个表格、无空行的贴表说明 | Passed |

命令与分层遵循[测试规范](../../testing-guidelines.md)。在官方基线 `7b752160` 的两处旧实现上运行新增表格集合，13 项因目标缺陷失败、6 项通过。两项表体 `th` 用例能发现把数据误识别成多层表头的内容丢失；多句条件的超长行从公开分块入口验证，恢复表格行的长度细分后会触发禁止嵌入的断言。

独立 checkout 只读挂载到既有 API 依赖镜像，使用 `--network none` 运行，不加载业务数据或替换服务。容器内 `backend` 工作目录执行 `python -m pytest test/unit/knowledge/chunking/test_semantic_tables.py test/unit/knowledge/chunking/test_semantic_chunking.py test/unit/plugins/test_chunk_source_lines.py -q -p no:cacheprovider`：38 通过，包含 19 项表格用例。

源提交 `d84ff247` 的只读原文重放证据：只读事务回读现有文档在 PostgreSQL 中的参数和旧块，再从对象存储读取实际原文进行重放，旧三块对应新 37 块，其中 35 个表格行块、三条独立申报说明；所有原表单元格文本均保留，首行学历只输出一次，来源行号有效，最大块为本地计数器的 304 tokens。该探针不写入业务数据，不代替重建索引后的检索效果评估；独立 checkout 的禁网验证不重跑此探针。

同一只读挂载环境执行完整后端 unit `python -m pytest test/unit -m "not slow" -q -p no:cacheprovider`：3663 通过、11 项部署配置测试因镜像未提供 Docker CLI 而跳过；仓库根文件在该环境可见。源码全目录与新增测试的 Ruff lint、format、import 检查通过；变更文档的相对链接检查和 `pnpm run build` 通过。

工程信任检查通过；其 56 项单元测试在 Linux 容器对同一源码执行 `python -m unittest scripts.ci.test_verify_engineering_contracts` 后全部通过。其他 OCR 引擎的识别效果、实际索引重建与检索召回率未验证。
