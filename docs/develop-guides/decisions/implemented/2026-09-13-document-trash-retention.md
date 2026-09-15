# 托管知识库文件30天回收站

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/document_trash_service.py

## 问题

同步硬删除不支持误删恢复；只隐藏列表也不足以阻止旧分块、图片与图谱继续进入新问答。

## 决策

Milvus托管文件与文件夹使用PostgreSQL生命周期状态保留30天。普通文件仓储、检索重排前后及图谱按活动来源过滤。删除不移除索引与存储对象；恢复原目录、不覆盖同名对象。处理中或占用任务的文件整批拒绝删除，状态更新与CAS拒绝复活回收站项。

DocumentTrashRepository拥有删除、恢复、租约、批次与清理对象快照事务。既有ARQ worker每小时领取到期项；外部副作用成功后才以当前token移除元数据，失败保留状态与对象快照并退避重试。共享对象等最后引用清理；知识库树锁保护目录与清理的引用判定。原件引用跨库检查；登记与清理通过统一引用锁串行化。

图片对象键包含file_id；旧平铺图片以活动Markdown的完整规范化对象键验证来源，响应no-store。图谱共享实体的混合来源属性暂时隐藏，删除来源时清空属性，后续重建补全。

前端操作、状态机制及升级边界分别由[使用教程](../../../intro/document-trash.md)、[生命周期](../../../mechanisms/document-trash.md)、[升级说明](../../../advanced/document-trash-upgrade.md)拥有。

## 替代方案

继续硬删除不满足恢复；只做前端隐藏不提供读取隔离；删除时复制全部对象到新桶增加故障面。选择保留原对象并过滤权威状态，代价是回收站继续占用空间与新增读取门禁。

## 后果

默认无手动永久删除。截止为UTC720小时，定时运行和故障重试可使实际释放晚于截止。恢复和清理互斥；开始清理后不恢复半毁文件。原目录不活跃或同名冲突需用户处理。含回收站的知识库不允许整库删除。API/worker/web须同步升级；旧版不识别deleted_at，不可直接回退上线。

当前全局存储引用锁偏向正确性，单次清理期间会短暂阻塞对象登记；大规模部署可在保持相同原子性下改成按对象锁。历史回答、已发送模型内容和用户独立保存记忆不自动撤回。

## 验证

- 真实PostgreSQL：升级幂等、30天边界、重复删除不续期、目录恢复/父冲突/同名冲突、任务排斥、并发claim与旧token、失败重试、父链与整库删除锁。
- 本轮自包含真实PG/Redis/JWT/TCP HTTP：管理员生命周期、匿名和普通用户拒绝、下载拒绝、整库删除拒绝、到期及处理中拒绝。原live API测试保留，但本轮不计入已执行结果。
- 本轮浏览器使用实际统一回收站页面和Ant Design控件、合成API数据，验证个人恢复3→2与成功提示、到期禁用、错误/空态及深色窄屏。没有执行真实登录后的文件/整目录删除，不把此页面验证写成生产E2E。
- 真实PG/MinIO/Neo4j/Milvus：故障注入→PG回滚→重试→逐存储回读，共享对象最后引用及并发清理、URL编码空格图片。
- 单测覆盖期限、图片精确匹配、缓存/树锁次序、读取隔离、重排期间删除再次过滤、worker调度注册；变更文件lint及前端构建作为提交检查。
- 前端全仓仅已知上游PDF测试在Windows路径编码失败，未在本PR修改该独立问题；不声明全仓全部通过。生产迁移/部署与真实模型答案质量不在本次验收内，物理存储测试使用合成向量。

在隔离测试环境的 `backend/` 目录复验；真实仓储测试使用专用 `TEST_TRASH_POSTGRES_URL`，本轮HTTP测试使用专用PG/Redis并自行启动合成JWT身份的TCP服务，不使用原live服务账号，禁止指向生产：

```bash
python -m pytest test/integration/repositories/test_document_trash_repository.py
python -m pytest --confcutdir=test/integration/services test/integration/services/test_unified_trash_http.py test/integration/services/test_unified_trash_migration.py
python -m pytest test/unit/services/test_document_retention.py test/unit/services/test_document_trash_service.py test/unit/services/test_document_trash_images.py test/unit/services/test_document_trash_worker.py test/unit/knowledge/test_trash_read_isolation.py
```

统一入口与个人文件生命周期见[统一回收站决定](2026-09-15-unified-trash-lifecycle.md)。

本轮提交证据：

本次独立上游候选在隔离Linux环境复验：后端unit 2143通过，知识仓储PG 25通过，真实TCP HTTP/连续迁移3通过，个人生命周期/Viewer/历史门禁26通过（个人HTTP使用ASGITransport）。四存储执行真实Neo4j停机后的失败与PG回滚、恢复后重试、最后共享引用、并行及跨库共享清理，均逐存储回读通过。

独立Reviewer发现并复验关闭两项：取消清理时保锁等待真实I/O结束（包括重复取消），恢复取得树与行锁后重新核对期限。新增负向检查已计入上述测试；独立复审未发现剩余阻塞项。提交、上游合并、生产部署不是这些测试结果的组成部分。
