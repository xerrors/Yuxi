# 知识文件处理归属与产物回收收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/knowledge/repositories/files.py

## 问题

文档作业在领取文件执行权前独立保存参数；失败领取仍能覆盖在途处理配置。索引代次发布只检查文件身份，和解析状态更新采用的作业租约边界不一致。上传暂存及 Office 预览缺少完整回收责任，图谱失败样本绕过文件代次可见性。导入作业重复持久化完整文件详情和已消费的上传参数。

## 决策

### 实现方案

文件 repository 在领取执行权的事务内合并处理参数，持有作业租约的处理沿 Job→File 锁序验证未过期执行权。文件仍拥有状态、代次与可见产物；作业租约决定当前执行方能否发布。解析与索引共用这一边界，过期执行方不能切换 active_generation。

上传 service 在现有对象桶维护固定七天、仅匹配 `yuxi-knowledge-staging=true` 标签的 MinIO 原生过期规则，保留其他生命周期规则；正式原件不携带暂存标签。上传 service 拥有暂存对象的有界生命周期，导入仍以持久回执实现重放；文件清理包含旧版生成的 Office 预览 PDF。当前的原件读取见[浏览器 Office 预览](./2026-11-25-browser-office-preview.md)。分块仓储的失败样本使用现有当前代次可见性条件。

导入新作业只保存有序文件身份和实际执行选项，处理器继续读取历史 pending payload。删除无 shipping consumer 的旧仓储及提交入口，不增加新的调度、锁或配置系统。

## 替代方案

- 保留独立参数写和文件 owner 字符串：实现短，但失败任务仍有副作用，租约过期后的发布窗口无法关闭。
- 给文件增加另一套租约：与已有作业租约重复，扩大恢复和锁序维护面。
- 导入完成即删除暂存：会破坏并发消费及不同导入身份复用暂存的现有语义；有界保留避免这一变化。
- 删除历史 payload 支持：会让部署前登记的 pending 作业无法重建。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 领取失败不改变处理参数 | 并发作业 | 文件 repository | PostgreSQL integration | 另一 owner 在处理时提交不同参数 | 通过 |
| 过期执行方不能发布索引代次 | 租约与发布 | 文件 repository | PostgreSQL integration | 不运行恢复扫描，直接过期后完成 | 通过 |
| 删除时回收历史预览 | MinIO 清理遗漏 | 知识库文件清理 | MinIO integration | 写入旧版预览键后删除文件并回读对象列表 | 通过 |
| 暂存有明确回收边界且回执可重放 | 对象生命周期 | 上传 service | MinIO integration、unit | 暂存过期后重放成功回执 | 通过 |
| 失败样本只显示有效当前代次 | PG 查询 | chunk repository | PostgreSQL integration | 删除文件与旧代次的失败分块 | 通过 |
| 新旧登记作业均可执行 | 持久 payload | 导入及 handler | handler unit、真实 PG 导入 integration | 历史 files payload | 通过 |

旧能力不存在：领取前独立参数保存、无租约的带 owner 代次发布、导入新作业中的文件详情快照、无 shipping consumer 的旧入口（包括 `add_file_record`、`update_file_params`、`get_kb_executor`）。

重新引入条件：真实 consumer 需要独立参数编辑或新持久协议时，必须定义事务、兼容与测试边界，不能恢复仅转发的旧路径。

## 后果

作业和文件锁序必须保持一致，不能让租约校验引入反向锁依赖。历史未标记暂存对象不会被静默纳入删除范围，需运维显式审计和补标；正式原件不属于暂存过期规则。生命周期更新需要桶配置权限，配置失败时上传明确失败。历史无作业的直接调用仍由文件状态条件约束；不得为测试伪造不存在的作业来绕过生产租约边界。

在隔离 PostgreSQL、MinIO 中执行 `python -m pytest test/integration/services/test_knowledge_projection_integrity.py test/integration/services/test_knowledge_object_lifecycle.py -q`，两文件分别为 16 项和 4 项通过。参数领取、过期发布、失败样本三个新增 PG 回归在修复前均按目标缺陷失败。`python -m pytest test/unit/routers/test_knowledge_documents_router.py test/unit/services/test_background_jobs.py test/unit/services/test_knowledge_job_handlers.py -q`：101 项通过。真实 MinIO 用例重读服务端生命周期配置、对象标签和内容，并在删除暂存对象后重放持久回执；不等待七天实际到期。完整 worker/ARQ 投递链路不属于这组局部验证，历史 payload 由处理器 unit 覆盖。

文件参数旧独立更新入口删除后，配置修复、继承、非法假值回滚和非分块嵌套选项替换改由真实领取事务验证。`python -m pytest test/integration/services/test_knowledge_metadata_boundary.py test/integration/services/test_chunk_config_boundary.py -q`：23 项通过；前者使用真实 PG 与 FastAPI HTTP 协议，正文仅在对象读取边界提供固定字节，覆盖损坏配置、禁止执行器构造、跨库身份及错误状态码。相关作业、路由、生命周期和只读连接器 unit 集合 132 项通过。
