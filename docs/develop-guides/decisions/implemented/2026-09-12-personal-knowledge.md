# 辅导人员个人知识库

状态：implemented
类型：feature
Owner：backend/package/yuxi/permissions/resource_permission.py

## 问题

辅导人员已有个人知识管理业务能力，但知识页面及 HTTP 依赖要求平台管理员。创建者归属已经持久化，文件维护及检索复用同一知识库权限解析。

## 决策

复用 created_by 与 version 2 的空读取、管理范围表示仅所有者的个人库，不新增表。具有 knowledge.personal.manage 能力的用户可创建和维护个人库；既有管理员共享库流程保留。个人库禁止改为共享，其他账号包含超级管理员无法读取。普通用户不得通过创建参数发布共享库。开放必要页面和资源接口，保留全局统计及无归属处理接口的管理限制。上传资料与挂载文件均验证目标知识库归属。

## 替代方案

另建个人库表会重复知识与文件机制；仅移除前端管理员限制无法闭合 HTTP 授权；提升全部普通用户的管理权限会扩大既有共享库权限。

## 后果

原仅所有者配置的知识库也执行严格所有者隔离。索引及模型问答不在无真实模型验收范围；本批验证文件上传、持久化和授权，不用模拟回答替代生成验证。

普通辅导人员创建入口仅开放 Milvus 文档库。评估、全局统计修复、URL 抓取及无归属文档转换保持管理员限制。前端在账号切换时清空知识库缓存，并丢弃旧身份的在途响应。

## 验证

真实 HTTP 测试 `test/integration/api/test_personal_knowledge_api.py` 通过。两个辅导账号分别创建个人库，PostgreSQL 回读创建者、私有配置及文件记录，对象下载内容与上传原文一致；无业务能力创建、发布共享配置、非 Milvus 创建、他人读写、技术管理员读取、修改共享、跨库文件地址及预处理路径均被拒绝。使用本人库 ID 搭配他人文件 ID 的查看、下载和删除失败，原文件保持完整。

临时辅导账号的浏览器验收完成知识库入口、创建、详情、描述更新和保存后回读；权限页截图显示“仅本人可见”，没有共享编辑控件。管理员专用统计修复按钮不向辅导人员渲染。测试账号及所属测试资料在验收后清理。

后端 `uv run --group test pytest test/unit -m "not slow"` 通过：1953 passed、53 skipped。工程契约检查及脚本自测通过（62 tests）；前端 lint、build 和个人库专项测试通过（7 tests）；文档使用已安装的 `vitepress build` 构建通过。

完整前端 `pnpm run test:unit` 执行 325 项，324 通过；Dashboard 搜索参数案例因扫描图标目录时 `ENOMEM` 失败，使用 `node --test --test-name-pattern getConversations test/unit/dashboard_thread_stats.test.js` 定向复跑通过。全量执行结果不记为全绿，失败案例复跑及个人库专项提供补充证据。
