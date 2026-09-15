# 统一回收站与个人文件恢复生命周期

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/personal_trash_service.py

## 问题

知识库文档、个人文件和正式会话附件从不同界面删除，用户需要同一个可找到的恢复入口。个人文件直接移走但继续引用旧附件、viewer或模型历史，会导致内容状态不一致。

## 决策

`/trash` 聚合当前用户个人回收条目与有管理权限的托管知识库，知识库仍按原库分页，不扩大授权。沿用[知识库生命周期决定](2026-09-13-document-trash-retention.md)；个人业务表从v7连续升级v8，仅创建journal，不引入结构化或企业记忆表。knowledge v2连续升级v3。

个人删除/恢复由持久意图、用户锁与no-follow文件原语闭合；正式附件元数据和物理路径同批迁移。已排队/运行任务阻止删除和恢复，pending journal阻止新请求接入。既有worker处理到期和中断恢复，不新建服务。

个人历史最终发送门禁与摘要门禁独立于知识库/企业记忆实现。缺少逐文件来源的历史按最早会话时间保守隔离，历史工具消费把内部时间标记传播到可信consumer及祖先；该标记由服务维护，不接受普通metadata更新覆盖。主动压缩先取用户文件锁再取会话锁，通过内部ContextVar复用持有事务执行最终门禁，避免自锁，并覆盖模型与checkpoint保存。

## 替代方案

只聚合前端列表没有恢复事务；只移动文件但不更新附件与读取路径会留下旧内容访问；额外复制所有文件增加中断一致性问题。沿用现有文件原语和业务数据库journal，统一入口复用现有表组件。

## 后果

保留30天不代表截止时立即释放空间，后台周期和失败重试会延后释放。到期后不恢复，同名冲突不覆盖。删除个人文件可能保守阻断较早创建但未使用该文件的会话，新建会话或恢复文件可继续，聊天记录本身不擦除。知识库已发送历史答案及用户独立保存记忆不承诺自动撤回。

API/worker/web同时升级；旧版本不识别删除与journal字段，不直接回滚到旧运行版本。public变更不包含私有部署、地址、品牌、账号或来源同步。

## 验证

可复验命令与作用域：

- `test/integration/services/test_unified_trash_migration.py`：显式隔离PG，新建与business7/knowledge2存量、重复迁移、旧版本拒绝和无关表不引入。
- `test/integration/services/test_unified_trash_http.py`：真实PG/Redis/JWT/TCP HTTP，管理权限、删除后下载隔离、恢复、在途/到期冲突与数据库回读。
- `test/integration/repositories/test_document_trash_repository.py`：真实PG树锁、租约、共享引用和处理中断。
- `test/integration/services/test_personal_trash_lifecycle.py`、`test_personal_trash_viewer.py`：真实PG与临时文件、应用路由、恢复冲突、附件和历史门禁；其HTTP客户端使用ASGITransport，不作为真实TCP登录证据。
- `test/e2e/document_trash_storage_probe.py`：显式独立PG/MinIO/Neo4j/Milvus，真实停机后失败回滚、重试、共享对象最后引用和并行清理；脚本要求专用数据库及显式环境开关。
- `web/test/unit/trash_drawer_runtime.test.js`、`personal_trash_runtime.test.js`、`api_boundary.test.js`：真实SFC交互和API错误投影。
- `web/test/browser/unifiedTrash.html`：实际页面及Ant Design控件、合成API数据，支持浅/深色、空态与错误状态；不连接生产。

本次独立上游候选复验：Linux后端unit 2143、知识仓储真实PG25、TCP HTTP/连续迁移3、个人生命周期/Viewer/历史门禁26均通过。个人HTTP测试使用ASGITransport。四存储真实故障后重试、共享最后引用、并行与跨库清理通过。独立Reviewer发现的取消释放锁及恢复时间过期问题已修复，并通过新增负例与独立复审。不以历史维护分支测试替代本次结果；本轮不验证生产部署。
