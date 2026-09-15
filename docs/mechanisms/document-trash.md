# 文档回收站生命周期

面向开发与运维，解释状态、读取隔离与到期清理；前端操作见[使用文档回收站](../intro/document-trash.md)。

## 数据流

前端→受知识库管理权限保护的API→DocumentTrashService→DocumentTrashRepository（PostgreSQL）。

knowledge schema v3新增删除时间、截止时间、删除批次、操作者、清理租约/token、错误及对象清单。普通文件读取过滤deleted_at；检索重排前后及图谱按活动来源过滤。新图片键包含file_id，旧平铺图片通过活动Markdown引用确认访问归属，响应不缓存。

删除事务只改生命周期并失效导图/示例问题，不提前删对象/向量。删除/移动/恢复通过知识库树事务锁串行化，文件处理状态与任务占用使删除拒绝；普通更新和CAS拒绝已删除文件。

## 到期清理

既有ARQ worker每小时整点运行purge_expired_documents，无需新常驻容器。每轮最多50项，逐项租约600秒、240秒触发取消；取消后仍保锁等待底层I/O结束，因此不是240秒硬上限；失败保留记录、错误状态及对象清单，1小时后可重试，不阻塞其他到期项。

首次清理保存原文件、解析Markdown、预览及图片对象清单。外部图谱/向量清理成功后删除PG分块，再删除没有其他文档引用的存储对象，最后以删除批次+当前租约token删除文件记录。部分失败保持不可恢复并重试；共享对象等最后一个引用清理时删除。

30天是UTC连续720小时，非自然月。清理计划周期和失败重试意味着实际释放可能晚于截止点；过期即关闭恢复，不额外延长恢复窗口。


跨库原件引用登记与清理先持有统一存储引用锁，同库清理再持有文件树锁直到元数据删除，防止并发任务相互跳过共享对象。登记在锁内重新检查原件存在，避免等待清理后登记失效路径。解析运行配置在树锁前完成，遵循缓存→树的锁顺序。图片先提取编码对象键，再解码整键比较，避免前缀误匹配和含空格文件名截断。

源码入口：`backend/server/routers/knowledge_router.py`；用例：`backend/package/yuxi/services/document_trash_service.py`；状态事务：`backend/package/yuxi/repositories/document_trash_repository.py`；调度：`backend/package/yuxi/services/run_worker.py`。

已发送的历史聊天与用户保存的独立记忆不自动擦除；本功能阻断删除源的新读取，不承诺撤回历史回答。

整库删除也遵循相同引用锁；其他知识库仍引用本库原件或本库目录内对象时拒绝整库清理，避免绕过共享原件保留。

## 个人文件和附件

`PersonalTrashRepository` 和 `PersonalTrashService` 拥有 business v8 的 `personal_trash_entries` journal。先提交 `pending_delete` 意图，再以 no-follow 文件原语把原内容移入用户回收目录；API和worker复用同一恢复器。用户级事务锁串行化请求接入、附件确认、删除、恢复和到期处理，在途或排队任务期间不修改个人文件。

`pending_delete → trashed → purging → purged` 是删除主链；`trashed → restoring → restored` 是恢复链。恢复检查原inode及同名冲突，绝不覆盖新文件。取消调用也等待文件线程停止后再释放锁。恢复/清理失败保留journal；worker每小时第10、30、50分钟继续处理，每轮最多50项。已清理journal保留为历史隔离标记，不保留文件内容。

`PersonalFileEvidenceMiddleware` 在主/子智能体最终模型输入前核对；摘要也执行同一检查。缺乏文件级来源的旧会话保守以最早历史时间与删除记录隔离，历史搜索/读取把来源最早时间传递到可信consumer会话及祖先。用户修改会话metadata不会覆盖该内部标记。这一保守策略可能阻止未实际使用该文件的旧会话，代价明确，不外推为知识库历史来源撤回或独立记忆内容擦除。
