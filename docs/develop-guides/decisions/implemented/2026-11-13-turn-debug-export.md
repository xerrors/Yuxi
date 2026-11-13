# Turn 调试导出与正文展示

状态：implemented
类型：feature
Owner：frontend/src/modules/session/model/messageDebug.js

## 问题

调试详情仅能复制选中对象的原始 JSON，难以按阅读需要导出整个 Turn。MCP 文本块中的 JSON 被再次序列化，概览出现嵌套转义和换行字符。

## 决策

### 实现方案

调试详情的三点菜单提供完整、精简、极简 Turn 的复制和 JSON 下载。选中 Turn 或其记录均以所属分组为范围，包含该 Turn 的所有已加载 Run 和记录，不受搜索和时间筛选影响。完整档复用现有 Turn 数据结构，保留原始字段；顶部会话级复制继续维持原行为。

`messageDebug.js` 拥有展示和导出投影，`MessageDebugPanel.vue` 只装配菜单、剪贴板和文件下载。精简档按白名单保留用户、模型正文与工具名称、参数、结果和错误，舍弃 reasoning、内部 ID、计时元数据及重复的模型工具声明。缺少对应工具审计时保留声明，避免丢失尚未执行的调用。极简档只保留用户和模型正文。

工具参数和结果分别以固定 `cl100k_base` tokenizer 截断，超过 300 token 时保留前 200 和后 100 token，中间插入省略标记；截断片段以字符串保存，外层 JSON 始终有效。新增 `js-tiktoken`，仅精简导出时按需加载轻量入口与单份词表，不根据不同模型猜测 tokenizer。

概览解析合法 JSON 和文本块，用纯文本展示换行及缩进；不盲目替换反斜线，也不执行 HTML。数据页与完整导出保留原始内容。

## 替代方案

- 按字符估算 token：依赖更少，但无法满足明确的首尾 token 预算。
- 由后端导出：增加接口与数据链路，前端已有持久审计投影足以完成当前需求。
- 直接修改原始记录：会损害完整导出和原始数据排障能力。

## 后果

固定 tokenizer 用于导出预算，不等同于各模型的实际计费 token。词表增加精简导出时的加载体积。完整仅指已加载审计数据；现有最新 500 条限制仍生效。工具截断只改变导出投影，原始审计和概览不截断。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 六个菜单操作导出所属 Turn，复制和下载一致 | 只导出选中工具或筛选子集 | MessageDebugPanel.vue | 浏览器 replay 回读并比较剪贴板与下载内容 | 选中工具并搜索后导出 | Passed |
| 完整保真，精简截断并去冗余，极简仅正文 | 超长输出或 reasoning 泄漏 | messageDebug.js | 相关 unit、浏览器 replay | 超过 300 token、重复工具声明、混合内容块 | Passed |
| Content 正常换行，原始数据仍可读 | 嵌套序列化或路径反斜线损坏 | messageDebug.js | unit 与浏览器 DOM | MCP JSON 文本块、普通路径与非文本块 | Passed |

相关单测由 `frontend/test/unit/messageDebug.test.js` 和 `frontend/test/unit/messageDebugTokens.test.js` 拥有。浏览器 replay 为 `frontend/test/browser/messageDebugExport.js`，覆盖浅色、深色、1440px、768px 和键盘菜单操作，下载文件与剪贴板内容逐档核对。

实际运行：相关 28 个 unit、前端全量 1073 个 unit、`pnpm run lint:check`、前端 build、docs build、工程契约检查及其 56 个 unit 通过。后端 `pytest test/unit -m "not slow"` 在进度 88% 时因 API 容器重启以 137 退出，未完成；本功能没有后端源码改动。浏览器使用确定性 HTTP 审计夹具，不构成真实数据库链路验证。
