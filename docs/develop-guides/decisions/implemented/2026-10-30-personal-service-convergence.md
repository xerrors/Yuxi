# Personal Agent 服务边界收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/services/personal_assistants.py

## 问题

助理已有专用 Backend 和 Project，通用服务却重复查询登记、重写能力和校验固定目录，并额外禁止本人 API Key。规则分散增加了普通 Agent 的调用成本与维护负担。

## 决策

### 实现方案

助理沿用普通用户、APP、Agent 和 Session 授权；仅在普通目录、会话及协作列表隐藏，不增加 JWT-only、API Key 禁用或登记 Project 复核。已知会话按普通权限查询及通讯，权限外的用户和 APP 仍被拒绝。助理没有自动消费协作消息或完成通知的唤醒机制。

默认工具、共享 Skill、直接 MCP 和知识库选择由 PersonalAssistantContext 声明，产品页面隐藏配置入口；资源沿用普通可配置字段、资源授权、执行 manifest 和配置指纹。个人 Skill 按普通解析加入，依赖工具按激活状态授权，不另设白名单或 Skills 开关。模型默认值、配置优先级及资料同步由[交互与执行边界收敛](2026-10-31-personal-assistant-policy-simplification.md)拥有；运行准备只消费 context.model，运行中 steer 保留当前 Run 快照。

轻量编辑使用普通 Agent 的 name、icon、config_json.context.system_prompt 结构。HTTP 模型只开放这些字段，配置合并及记录更新复用现有 prepare_agent_config_write、merge_agent_config_json 和 AgentRepository.update_record；不创建独立偏好存储，也不覆盖整个 Context。

助理登记绑定既有 Agent、Project 和主 Session，执行时直接使用普通 Project/Workdir 解析。部署开关在 Backend 启动注册与产品入口生效；执行中不重复查询登记或比对固定路径。关闭前完成在途任务，不支持热切换撤销。

助理工具调用共享服务的 create_task，确定普通默认 Agent、root 和新的 implicit Project；create_session 继续统一负责锁、幂等、持久化和派发。默认工具由 Backend 拥有，创建、编辑、任务摘要与产品限制集中在助理服务，删除独立 policy 和任务摘要文件。停止、撤回待处理输入和恢复队列沿用普通机制。

## 替代方案

- 保留逐入口补丁：重复查询和多份配置规则会继续增长，拒绝。
- 新建策略框架或复制委派事务：增加维护面且没有新增消费者，拒绝。
- 删除普通权限与路径检查：会破坏既有隔离，不采用。

## 后果

列表隐藏不构成权限边界，本人 Key 可访问已知助理会话；不新增专用对外 API。工作目录由现有 Project 负责，助理不要求 Worker 再次比对字面路径。普通压缩沿用 Session 快照；Options 默认值不覆盖已有非空会话模型，已接收输入与运行配置保持冻结。

## 验证

以下为服务收敛时的证据；后续删除产品禁令与配置重建分支的最终证据见[交互与执行边界收敛](2026-10-31-personal-assistant-policy-simplification.md)。

- 后端全量 unit：3525 passed、125 skipped；独立审查修复后，Context、模型默认来源、manifest、压缩、快照和 Backend 注册的定向集合 117 passed。
- 真实 PostgreSQL/HTTP：登记、权限、委派和历史共 17 passed；初始快照修复后，登记及委派集合 12 passed。覆盖本人 full/agents Key 可访问、跨用户/APP 拒绝、专用模型不使用普通默认值、列表隐藏、Project 和来源回读。
- 真实 API、worker 和文件 E2E 3 passed：静默与显式发送、主对话停止限制、独立 root 成功文件与失败终态。测试使用确定性模型，不证明真实供应商输出质量。
- 独立 Astra Reviewer 检出首次空模型来源和固定资源 manifest 两项遗漏，修复后复核未发现新增问题；Reviewer 未代替执行测试。
- Ruff 源码、格式及导入检查通过。测试与验证入口由 backend/test/unit/agents/test_personal_assistant_runtime.py、backend/test/integration/services/test_personal_assistant_registration.py、backend/test/integration/services/test_personal_assistant_cooperation.py 和 backend/test/e2e/test_personal_assistant_e2e.py 拥有。

旧能力不存在：require_personal_access、restrict_personal_context、personal_policy.py、personal_tasks.py 已删除；Worker 与压缩不识别 Personal 登记或固定目录。

重新引入条件：新增明确的产品权限契约且普通权限不能表达时，在实际副作用边界实现；不恢复逐层重复登记查询。
