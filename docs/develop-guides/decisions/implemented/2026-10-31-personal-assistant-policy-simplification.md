# 个人助理交互约定与通用执行边界收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/services/personal_assistants.py

## 问题

个人助理把页面交互选择扩散成输入、协作、取消和配置用例中的特殊拒绝；通用服务重复识别 Backend，目录分配重复取得无关锁，创建页面又为已幂等的操作维护不可重试状态。这些分支增加维护成本，并使显式输入意图与实际执行不一致。

## 决策

### 实现方案

助理页面发送明确的 steer 参数并隐藏停止与配置选择；通用输入和控制服务按既有协议处理，不再重写意图或拒绝个人助理。协作目标复用 Agent 可见性，Project 目录分配复用 owning service 的锁。用户、APP、Run/lease、事务、幂等与文件路径边界保持由现有 Owner 执行。

Personal Context 只声明默认资源与隐藏的编辑项，资源与审批模式都进入普通可配置字段快照和执行指纹，不为固定值增加 manifest 例外。Agent 创建、删除和主会话归档仍保留唯一登记与固定入口的生命周期约束；通用编辑、Skill 绑定、定时任务及协作目标沿用普通权限。

共享 input_config 是新会话默认值的唯一解析入口。显式模型优先于 Agent 配置，助理缺省模型使用 personal_assistant_model，不回退到普通 default_model。助理可在未配置模型时创建主入口，首次需要默认值的输入补齐模型并保存到 Session；已有非空模型不随 Options 变化。输入的显式模型与审批覆盖、线程设置、活跃 steer 都遵循普通协议，不再按 Backend 重建整个快照。页面按 Session、Agent、专用默认值的顺序检查实际模型可用性。

助理资料编辑沿用公共配置合并，并按 Agent → User → Session 顺序取得锁，仅同步提交的名称和交流偏好到主会话；不改写已接收 Input 或当前 Run。创建失败保留表单并允许再次提交，后端唯一登记与用户锁负责幂等，不维护“响应未知后永久禁用”状态。已由类型、数据库外键或准备流程保证的重复兜底移除。

本决定更新[登记与入口](2026-10-29-personal-assistant.md)、[运行机制](2026-10-30-personal-assistant-runtime.md)和[服务收敛](2026-10-30-personal-service-convergence.md)中的产品限制与模型默认值语义。

## 替代方案

- 保留散落的产品拒绝：与前端已有选择重复，拒绝。
- 新增统一策略、能力标记或 Backend 生命周期框架：当前没有必要消费者，拒绝。
- 产品交互由前端声明，真实授权由已有边界负责：采用。

## 后果

专用模型是默认值，不是逐条消息重写的产品策略。管理员改变默认值不会覆盖已有会话的模型；显式输入只覆盖该次输入，长期调整使用普通线程配置接口。助理页面仍隐藏模型、审批和停止入口，后端按普通协议执行已授权请求。原有历史快照不回写。

保留普通用户与 APP 隔离、Run 执行租约、事务与幂等、Project/Session 锁，以及目录 no-follow 边界。这些约束拥有真实并发、权限或持久化失败面，不以页面隐藏代替后端授权。

## 验证

- 独立 Compose API 容器执行 `uv run --no-sync --group test pytest test/unit -m "not slow" -q -p no:cacheprovider`，测试进程设置 `YUXI_KNOWLEDGE_ENABLED=true`：3525 passed、125 skipped。只读挂载完整仓库后补跑 `python -m pytest test/unit/config -q -p no:cacheprovider`：196 passed、11 skipped；剩余项要求容器内 Docker Compose CLI，不计为通过。
- `uv run --no-sync --group test pytest test/integration/services/test_personal_assistant_registration.py test/integration/services/test_personal_assistant_cooperation.py -q -p no:cacheprovider`：13 passed，真实 PostgreSQL/HTTP 验证并发唯一创建、用户/APP 隔离、初始缺省模型、线程设置保留、模型可用性、资料同步、委派审批及幂等。
- 临时独立测试账号与确定性模型端点运行个人助理 E2E、跨树独立会话与并行等待场景：6 passed。真实 HTTP、worker、数据库和文件回读覆盖 follow_up 未被改写、取消后终态、整树停止/继续、独立目录产物和失败结果。初次复跑遇到回放端口与复用账号状态干扰，不作为通过证据；隔离账号后的回归不修改测试 oracle。
- 前端 `pnpm run test:unit`：996 passed；`pnpm run lint:check` 和 `pnpm run build` 通过。浏览器显式拦截创建请求并连续模拟两次连接失败，DOM 确认按钮恢复可用、名称与头像意图保留，未向真实服务创建助理。
- 恢复输入时整份重建快照的负控在独立测试进程执行，模型持久值由 p:first 被覆盖为 p:second 时，集成测试在对应断言失败。负控不修改源码、fixture 或 expected output。另一个负控将编辑恢复为 Session → Agent 锁序，真实 PostgreSQL 并发测试复现 DeadlockDetectedError；正确锁序下同一测试通过。锁序修复后，个人助理 3 项 worker E2E 再次通过。

- 源码 Ruff、格式及导入检查、工程契约脚本及其 56 项 unittest 通过。独立 Reviewer 复核完整需求、diff 和配置/锁序修复，未发现剩余可行动问题。未执行完整 E2E 套件或 CI；确定性模型不证明真实供应商输出质量。

旧能力不存在：后端强制 steer、个人主会话停止禁令、协作目标 Backend 禁令、通用管理/Skill/定时任务中的重复拒绝、重复环境锁、整份个人输入快照重建和不可重试创建状态均已删除；固定资源的快照及 manifest 特例不再需要。

重新引入条件：出现现有授权、事务或数据约束无法表达的真实问题，提供可复现反例后单独评审，不新增没有当前消费者的策略层或配置来源标记。
