# Skill 运行时解析与 Middleware 职责收敛

状态：archived
类型：simplification
Owner：backend/package/yuxi/agents/skills/runtime.py

## 问题

Skill 的存储和授权边界已经由共享 Skill 投影与个人 UserWorkspace 拥有，但
`agents/middlewares/skills.py` 曾同时拥有 Agent 请求生命周期和 Skill 运行时解析。
因此 Agent context、工具注册和安装工具需要反向依赖 Middleware 模块，模块职责与调用
方向不一致，并增加循环导入和迁移成本。

## 决策

`agents/skills/runtime.py` 统一拥有从已授权 Skill 列表派生 Prompt 元数据、依赖映射、来源
映射、依赖闭包、当前 Run scope、ToolNode 门控工具和已激活依赖包的确定性逻辑。该模块只能
通过现有 repository/service 授权入口获取 Skill，不能根据 slug 或文件存在性自行扩大权限。

`agents/middlewares/skills.py` 只保留请求包装、Prompt 注入、模型工具可见性门控、MCP 按需
加载和 `read_file` 动态激活。Agent context、工具注册和安装工具直接依赖 runtime 模块，
不再把 Middleware 当作解析 API。

实现删除未使用的 `skills_context_name` 初始化参数、无调用的 `get_prompt_metadata()` 和
`get_dependency_map()`，以及依赖包中没有消费者的 `skills` 字段；仓库没有旧 Python API 的
公开兼容承诺，因此不保留 re-export 或第二份实现。

Skill 文件与安装由 `services/skills` 按来源拥有，共享投影由
`services/skills/projection.py` 拥有，数据库索引由 `repositories/skill_repository.py` 拥有；Middleware 的激活时机、MCP best-effort 语义、共享/个人路径与
Sandbox 来源校验不变。

## 替代方案

- 保持所有解析函数位于 Middleware：改动风险最低，但不能解决反向依赖和职责混杂。
- 移入 Agent 业务 service：会把存储、安装、缓存、授权和运行时编排重新堆入同一服务，
  因此不采用。
- 保留 Middleware 兼容门面：仓库没有公开承诺或真实消费者证据，保留它只会制造长期维护面。
- 删除整个 Middleware：工具可见性门控、动态激活和 MCP 生命周期仍需要请求级 Owner，不能删除。

## 后果

- 纯运行时解析只有一个语义 Owner，依赖方向变为 context/toolkits/Middleware → runtime。
- 授权结果、依赖顺序、循环与缺失依赖的 fail-safe、来源映射、工具注册和激活时机保持不变。
- `runtime.py` 会依赖现有 Skill repository/service 和工具注册表，但不拥有文件写入、缓存或权限策略。
- 仓库外若未来出现旧 import 消费者，需要另立有期限和 Owner 的兼容决策，而不是恢复隐式门面。

## 验证

- `pytest test/unit/agents/skills/test_skill_runtime.py test/unit/middlewares/test_skills_middleware.py test/unit/agents/test_context_auth.py test/unit/toolkits/test_install_skill.py -q`：30 passed。
- `pytest test/unit -m "not slow" -q`：1400 passed，35 skipped。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，后者 62 passed。
- 受影响 Python 文件的 Ruff check、format check 与 `git diff --check`：通过。
- runtime 单测覆盖授权选择、共享/个人来源、依赖闭包、循环、缺失目标、重复依赖和依赖包字段。
- Middleware 与装配单测覆盖未激活工具隐藏、激活后 ToolNode 可执行、不可读 Skill 拒绝动态激活、
  Agent context 派生和安装后运行时元数据更新。

旧能力不存在：

- `SkillsMiddleware` 不再接受或保存 `skills_context_name`。
- `get_prompt_metadata()`、`get_dependency_map()` 和 Middleware 内的纯解析定义不存在。
- 依赖包不再返回无消费者的 `skills` 字段。
- context、工具注册和安装工具不再从 Middleware 导入 Skill 解析函数。
- 仓库内不存在第二份解析实现或旧兼容 re-export。

重新引入条件：

- 只有发现明确版本承诺或真实仓库外消费者时，才通过新的兼容决策引入有删除期限的 re-export。
- 只有产品需求改变共享/个人 Skill Owner、路径或授权语义时，才另立 storage/security 决策。
- 只有性能证据证明读取或依赖解析成为瓶颈时，才另立缓存决策并定义 uid 隔离与失效语义。
