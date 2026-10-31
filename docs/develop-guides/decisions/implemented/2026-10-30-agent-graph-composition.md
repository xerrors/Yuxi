# Agent 执行图与能力装配分离

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/graph.py

## 问题

Personal 为复用运行流程继承 Chatbot，随后关闭 Skills、替换协作工厂并遍历中间件裁剪工具。具体产品默认能力与通用执行机制耦合，工具被删除后仍可能残留提示词、钩子和状态。运行与清理模式在多个 Backend 和公共入口重复判断，使局部维护流程扩散到正常能力装配。

## 决策

### 实现方案

Chatbot 和 Personal 分别继承 BaseAgent，仅在 get_graph 声明状态类型、Prompt 构造函数、文件工具和中间件序列。公共构图入口集中处理运行与 checkpoint 清理的差异：运行时准备 Skills、文件后端、记忆、模型和业务工具；清理时只准备状态 API 所需的图，不加载这些外部资源。Prompt 函数在运行分支内求值，不再通过异步能力回调跳回 Backend 装配资源。

公共入口按同一顺序装配中间件，Personal 与 Chatbot 共用 Skills 解析和中间件。Personal 的共享 Skill、预加载 Skill、直接 MCP 与知识库选择默认为空列表，个人 Skill 仍自动加入；其工具和 MCP 依赖按普通激活与授权规则开放。Personal 明确选择文件工具和只读记忆。协作中间件构造不读取外部资源，两种路径均保留节点及状态声明；工具复用与助理沟通职责由[协作工具归属](2026-10-31-cooperation-tool-ownership.md)细化。权限和工具执行仍由现有边界负责。

工具配置是直接业务工具的唯一来源，不另设 tool_allowlist 或 skills_enabled。Chatbot 显式向公共装配入口提供基础工具，Personal 默认直接选择两个 Web 工具；未装配工具不会进入 ToolNode，已装配的 Skill/MCP 依赖继续受普通执行授权约束。

文件中间件的构造函数直接拥有安全的默认工具列表，并把明确传入的工具转换为上游要求的 list。性能探针包装公共执行模块的真实资源加载调用点。

## 替代方案

- 保留继承并增加开关：能力选择继续分散，拒绝。
- 各自复制完整执行流程：容易使授权、取消和重试顺序漂移，拒绝。
- 把 Chatbot 改名为公共基类：仍默认装配普通产品能力，拒绝。
- 新增公共 ReAct 基类：当前只有两个调用方，增加继承层不能减少实际能力差异，拒绝。
- 每个 Backend 分别处理清理：模式判断随能力拆分扩散，拒绝。
- 公共入口集中运行资源准备与清理分支：采用，Backend 只声明当前已有能力的差异。
- 全面改造为延迟绑定模型和动态工具的纯构图：涉及实际执行装配，与收敛取消分支的目标不相称，拒绝。

## 后果

业务接口、消息和委派协议保持原有语义；公共运行层不需要识别 Personal。新图的取消清理继续沿用现有状态 API；Chatbot 清理图保留真实协作中间件注册，保持 before_model 节点与通道一致，状态清理不执行运行钩子。验证环境不要求兼容旧 checkpoint，不添加历史状态迁移或 fallback。

## 验证

- `docker exec yuxi-v1-latest-api-1 uv run --no-sync --group test pytest test/unit -m "not slow" -q`：3524 passed、125 skipped。包括真实构图工具集合、个人 Skill 依赖激活、模型快照、审批及取消清理；跳过项不计作已验证能力。
- 个人助理登记和委派的 PostgreSQL 隔离 Schema / HTTP integration：13 passed；新增配置合并保留原字段断言后，登记文件 10 项重跑通过。覆盖唯一创建、用户隔离、受限编辑、专用模型冻结、关闭开关和独立 root 委派。
- MCP 与 Backend 工具同名在最终装配处明确失败，本地重复工具不误拒；实际构图负例防止上游 ToolNode 按名称覆盖。
- 前端 lint、类型检查和构建通过，996 项 unit 通过。主环境 5173 浏览器验证提示词保存、刷新恢复及原值还原；通过 send_to_user 返回 43×19 的结果 817，数据库 Run 为 completed。
- 真实委派创建普通 Agent 的独立 root，默认无项目选择；任务返回 29×31 的结果 899，右侧复用聊天历史显示该结果。
- 后端 Ruff、格式检查、工程契约及其 56 项测试、文档构建通过。未执行完整 E2E 套件或 CI。

旧能力不存在：Personal 对 Chatbot 的继承、能力工厂覆盖、事后裁剪工具，以及 tool_allowlist、permits_tool、skills_enabled、default_model_option 已删除；两个 Backend 中不再有 checkpoint_only 条件分支，模式只在公共构图入口的一处 if/else 处理。

重新引入条件：出现实际不能由显式中间件组合表达的执行拓扑，再另行决策；不为假想第三类 Agent 扩展框架。
