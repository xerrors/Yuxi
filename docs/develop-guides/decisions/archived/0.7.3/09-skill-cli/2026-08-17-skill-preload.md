# Skill 预加载

状态：archived
类型：feature
Owner：backend/package/yuxi/agents/skills/runtime.py

## 问题

Agent 的普通 Skill 只在系统提示中提供名称、描述和 `SKILL.md` 路径。模型通过 `read_file` 读取根级说明后，Skill 才进入动态激活状态并向后续模型调用开放依赖工具。对于 `knowledge-base` 这类需要从首轮稳定可用的能力，该流程会增加一次模型判断和工具往返，也可能因模型没有主动读取说明而无法使用已配置能力。

系统需要允许 Agent 显式指定少量预加载 Skill，使完整根级说明和依赖工具从首轮模型调用起可见，同时保持现有可见范围、权限过滤和其余 Skill 的渐进加载语义。

## 决策

`BaseContext.preload_skills` 是默认空列表，并复用 `kind="skills"` 的配置选项。它不是新的资源授权入口：配置归一化先解析 `skills`，再把预加载根 Skill 限制为该列表的子集。`prepare_agent_runtime_context` 先按当前部署模式过滤运行时 Skill，再沿依赖图展开预加载闭包，从本次权限解析得到的真实 `source_dir` 读取每个 Skill 的根级 `SKILL.md`，并把有序内容快照保存在本次 Graph 的私有 Context 字段中。读取从文件系统根目录描述符开始逐段使用 `O_NOFOLLOW` 打开来源目录，再以同样约束打开根文件并验证为普通文件；缓存命中后任一祖先目录或根文件被替换成符号链接都会 fail-closed。

`agents.skills.runtime` 拥有授权后的 Skill scope、依赖闭包和预加载内容读取。`SkillsMiddleware` 只在每次模型调用中基于原始 request 注入预加载完整说明，并把预加载闭包与 checkpoint 中仍可读的动态激活 Skill 合并为本轮有效激活集合；它不修改持久配置、`context.system_prompt` 或 checkpoint。依赖工具沿用现有本地工具注册与 MCP 加载路径；不同来源的同名本地或 MCP 工具在 Graph 构建期显式失败，避免模型 schema 与 ToolNode 执行对象分叉。

预加载只包含根级 `SKILL.md`，不递归拼接 references、scripts 或 assets。配置为空时不读取文件、不注入完整说明，也不改变工具可见性。预加载文件不可读时 Graph 创建显式失败，不静默退回懒加载。`knowledge-base` 是使用场景而非硬编码默认值；现有 Agent 配置不迁移，运行时继续按用户授权与 Agent Skill 列表过滤它。

运行清单沿用现有 schema。`preload_skills` 作为规范化 context 的一部分进入 `config_digest`，共享 Skill 的目录摘要由 `resources.skills[].content_hash` 记录，实际预加载根说明以 `preload_content_hash` 记录；个人 Skill 不借用同 slug 共享记录的版本和摘要。worker 在固化 manifest 时生成同一份内存执行快照并传给 Graph，重试重新解析后的 fingerprint 必须与 write-once manifest 一致，否则在执行前 fail-closed。

## 替代方案

- 新增独立预加载 Middleware：会与 `SkillsMiddleware` 重复拥有提示注入和依赖工具门控，形成两套激活语义。
- 在 `build_prompt_with_context` 中拼接 Skill：该函数不拥有 Skill 权限、依赖和工具可见性，也会让主 Agent 与 SubAgent 的装配分叉。
- 把预加载 Skill 写入 LangGraph `activated_skills`：预加载来自当前 Agent 配置，动态激活属于 checkpoint 状态；持久化两份事实会使配置变化和 resume 产生歧义。
- 默认预加载全部可用 Skill：会破坏渐进加载契约并稳定放大 prompt 与工具 schema，因此默认保持为空。

## 后果

每个预加载 Skill 会增加每轮模型输入，并从首轮暴露其依赖工具；调用方应只选择确实需要稳定首轮可用的少量能力。完整说明不做静默截断，过大的 Skill 应由内容 Owner 拆分 references 或取消预加载。

预加载内容只能来自权限解析后的共享投影或个人工作区真实来源，用户传入的 slug 不参与宿主机路径拼接。旧 Agent 缺少字段时由 schema 默认得到空列表，不需要数据库迁移。主 Agent 与 SubAgent 复用同一 Context 和 Middleware 语义，SubAgent 原有的禁用工具过滤仍在模型调用前执行。默认空不读取 Skill 文件，也不增加首轮 prompt 或工具 schema。

## 验证

| 验收主张 | 直接证据 | 结果 |
|---|---|---|
| 默认空配置保持渐进加载；预加载从首轮注入完整说明并开放依赖工具 | `UV_PYTHON=3.13 uv run --directory backend --group test pytest test/unit/agents/skills/test_skill_runtime.py test/unit/middlewares/test_skills_middleware.py -q` | Passed；覆盖默认空、依赖闭包、完整说明注入和首轮 MCP 工具可见性 |
| 预加载不能越过 `skills` 或用户权限，文件来源拒绝祖先目录 symlink | `UV_PYTHON=3.13 uv run --directory backend --group test pytest test/unit/agents/skills/test_skill_runtime.py test/unit/agents/test_context_auth.py -q` | Passed；覆盖未选择 Skill、授权后闭包和 no-follow 读取 |
| `preload_skills` 作为规范化配置进入现有 Run 指纹，不新增 manifest schema | `UV_PYTHON=3.13 uv run --directory backend --group test pytest test/unit/services/test_agent_run_manifest_service.py -q` | Passed；仅改变预加载配置时现有 `config_digest` 变化 |
| 首个模型请求必须同时包含完整 Skill 说明和依赖工具 schema，真实 shipping 链路仍需服务环境复核 | `NO_PROXY=localhost,127.0.0.1 UV_PYTHON=3.13 uv run --directory backend --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -q` | Replay contract Passed；依赖 API、worker、PostgreSQL 和 sandbox 的 2 个用例在当前环境 skipped，不能记为链路通过 |
| 受影响 Python 文件满足静态检查与格式约束 | `UV_PYTHON=3.13 uv run --directory backend ruff check <受影响文件>`；`ruff format --check <受影响文件>` | Passed |
| 主 Agent、SubAgent、工具门控及其余后端 unit 回归保持成立 | `UV_PYTHON=3.13 uv run --directory backend --group test pytest test/unit -m "not slow" -q` | Passed：1463 tests |
| 工程契约和正式文档保持可验证 | `python3 scripts/verify_engineering_contracts.py`；`python3 -m unittest scripts.test_verify_engineering_contracts`；`pnpm exec vitepress build --outDir <临时目录>` | Passed：25 decisions / 5 workflows / 4 agents files / 66 docs；61 contract tests；docs build 成功，只有既有构建 warning |
