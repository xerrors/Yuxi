# GUI 报告第一部分剩余缺陷

状态：implemented
类型：bug-fix
日期：2026-10-12
Owner：backend/yuxi/modules/agents/services/cooperation.py

## 问题

报告第一部分剩余 D2、D3、D11、D21、D23、D24、D26。标题未参与会话搜索，沙盒缺失文件响应触发 SDK 校验错误并把内部细节交给模型，自动标题允许占位词、思考标记和无关主题，协作树没有创建深度限制，文件列表使用旧统计缓存。已修复条目与标记为不修复的裁决保持各自范围。

## 决策

### 实现方案

SessionRepository 在原有用户、APP、Agent 与活动状态作用域内联合匹配标题及可见消息；标题单独命中时不伪造消息片段或计数。搜索参数和返回协议沿用现有接口。

ProvisionerSandboxBackend 在 SDK 响应解析边界识别结构化的文件不存在错误，向同步与异步工具返回稳定语义；其他响应校验失败不透出 Pydantic 模型、输入与堆栈。文件工具允许并行，读取先于写入完成时仍明确返回不存在。

自动标题服务保留事务内先写用户输入截断的机制，清除模型思考段与标记，拒绝空结果、占位词及残留会话引用协议；模型失败或候选不可用时保留输入截断。字词关联限制由[自动标题只校验格式](2026-11-01-session-title-format-only.md)取代，候选不要求复用原文字词。一次命名与手动改名优先级保持不变。

system_options 增加整数最大协作深度，默认 2，根会话为 0。SessionCooperationService 根据持久调用者的派生路径，在新建子会话和 Input 前拒绝超限；已有幂等创建结果继续重放。系统设置提供对应配置入口，配置校验由现有 Option 边界拥有。

KnowledgeFileRepository 的文件统计读取真实 PostgreSQL 聚合，移除短 TTL 统计缓存，避免上传后列表与统计跨版本；文件及统计事实仍由数据库拥有。

## 替代方案

为统计缓存增加版本号和写入失效机制会扩大所有文件修改入口的维护义务，当前修复使用直接聚合。标题格式与相关性校验的替代方案由[标题格式决策](2026-11-01-session-title-format-only.md)维护。协作深度在提示词内约束无法阻止直接工具调用，限制放在产生持久副作用的服务边界。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 标题或消息均可搜索，作用域及分页保留 | 搜索 SQL / HTTP | SessionRepository | 真实 PostgreSQL、HTTP、页面 | 恢复内连接消息匹配：真实 PG 标题搜索失败 1 项 | 通过：HTTP 2 项、PG 1 项、标题单独命中 DOM |
| 缺失文件与异常响应没有内部校验文本 | Sandbox wire / tools | ProvisionerSandboxBackend | SDK 响应矩阵与真实文件读写 | 恢复原 SDK 错误输出：同步/异步旧版、当前与异常响应失败 6 项 | 通过：SDK 6 项、真实沙盒不存在→写入→读回 |
| 标题过滤思考与占位，规范化格式，失败保留用户输入 | LLM / 持久化 | titles.py | 真实 PG 读回、生成与失败矩阵、改名竞争 | 超时、429、占位、思考段、协议与并发改名 | Passed：当前证据见[标题格式决策](2026-11-01-session-title-format-only.md#验证) |
| 超深创建没有 Session/Input 副作用，深度 2 可创建 | 协作创建 / 幂等 | cooperation.py、system_options | 真实 PG、协作 E2E、配置 HTTP与页面 | 移除服务 guard 失败 3 项；移除整数校验失败 6 项；移除页面输入 guard 失败 1 项 | 通过：PG 深度 0/1/2、幂等重放、真实 worker 拒绝深度 3，配置 HTTP 与页面 |
| 上传后文件统计与数据库及列表一致 | 文件写入 / Redis / 页面 | KnowledgeFileRepository | 真实 PostgreSQL、HTTP、两次上传 DOM | 恢复旧缓存：第二次上传统计失败 1 项 | 通过：PG/Redis 旧缓存 300 秒案例，真实页面两次上传，列表/顶部/详情均为 2 |

## 后果

直接聚合增加大知识库统计查询成本，沿用 kb_id 索引与已有分页请求频率。标题格式校验不判断概括质量，模型调用或格式校验失败时保留用户输入截断。深度设置缩小默认协作树，已有超过限制的会话保留；新创建被拒绝且幂等重放不受影响。测试资源使用唯一用户、Schema 或受测试清理拥有的文件，保留用户数据。

## 验证

测试使用主 Compose API/worker、PostgreSQL、Redis 与实际沙盒；数据库服务用例创建独立 Schema，HTTP、浏览器和最终协作 E2E 使用临时 superadmin 账号。外部模型配额与坏标题通过确定性响应模拟，标题最终值重新从 PostgreSQL 读取；未重复消耗外部商业模型配额。

- `docker compose exec api uv run --group test pytest test/unit -m "not slow"`：2726 passed、60 skipped（既有环境/可选测试）。
- `docker compose exec frontend pnpm run test:unit`：570 passed；lint 与 build 通过。
- 相关真实数据库集成用例：76 passed；补充标题 APP/Agent/字面匹配、标题持久化与 SDK 响应：19 passed。
- `test/integration/api/test_public_session_resources.py` 的搜索与配置用例：2 passed。
- `test/e2e/test_session_cooperation_e2e.py` 的基本共享沙盒与第三层拒绝：2 passed；隔离深度配置后再运行第三层拒绝：1 passed。
- 独立副本中的负向回放恢复旧 SQL、SDK 错误、标题处理、统计缓存和整数配置，分别产生 1、6、8、1、6 项预期失败；移除服务深度 guard 产生 3 项预期失败，页面输入 guard 产生 1 项预期失败。生产工作树没有为负向回放修改。
- 浏览器验证真实标题搜索和两个文件上传的 DOM/HTTP，设置标签、说明以及浅色、深色、375px 视口截图位于本地 `docs/vibe/gui-report-evidence/`（不作为唯一完成证据）。

标题字词关联限制及对应旧测试预期被[标题格式决策](2026-11-01-session-title-format-only.md)部分取代；格式校验不证明模型概括语义正确。

工程信任检查、64 项脚本单测、修改文档相对链接检查、VitePress 构建及 `git diff --check` 通过。
