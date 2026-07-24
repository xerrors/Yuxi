
# 项目目录结构 (Project Overview)

Yuxi 是一个基于大模型的智能知识库与知识图谱智能体开发平台，融合了 RAG 技术与知识图谱技术，基于 LangGraph v1 + Vue.js + FastAPI + LightRAG 架构构建。项目完全通过 Docker Compose 进行管理，支持热重载开发。

架构代码地图见 [ARCHITECTURE.md](ARCHITECTURE.md)。修改不熟悉的模块前，先阅读其中的后端、前端、运行链路和架构不变量说明，再用符号搜索定位具体实现；该文档只维护相对稳定的系统边界，不替代细节文档或源码注释。

## 开发准则

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- Restate the request as the smallest acceptance criteria you are about to satisfy. If you cannot state it simply, you do not understand the request yet.
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.
- Treat phrases like "可以", "也可以", "类似这样", or "for example" as acceptable simple directions, not permission to design a larger mechanism.

## 2. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## 代码整洁规范 (Code Cleanliness)

### 核心原则

1. **简单直接**：使用能满足验收标准的最小实现，不增加未被要求的功能、配置、兼容层或扩展点。
2. **职责完整**：相关逻辑应集中在最容易理解的位置，优先保持主流程线性、完整、可一眼读懂。
3. **最小改动**：每一处变更都应能直接追溯到当前需求；不顺手重构、格式化或清理无关代码。
4. **显式失败**：预设条件不成立时及时暴露错误，不使用静默回退、冗余保底或吞异常来掩盖设计问题。
5. **阅读优先**：代码首先服务于维护者理解，其次才是抽象复用；抽象后调用链更绕时应选择更直接的写法。

### 实现细则

- 不为单次使用的逻辑创建抽象，不为想象中的未来需求预留“灵活性”或“可配置性”。
- 不为简单线性逻辑拆出一组细碎 helper。拆函数必须用于明确复用、隔离副作用或实质降低认知负担。
- 遵循向下规则（The Stepdown Rule）：公开、高层方法在上，实现细节逐层下沉；读者应能从上到下连续理解调用关系。
- 优先使用早返回和清晰的主路径，避免不必要的嵌套、间接跳转、聚合层、优先级规则、协议解释器和多层 fallback。
- 常量和具名值应放在最小合理作用域：跨函数复用、协议标识、配置约束或需要全局统一维护时使用模块级常量；仅服务单个函数的错误文案或局部规则，即使需要复用，也优先定义为函数内具名变量。不要为了减少变量而强行内联，也不要把局部实现细节提升为全局状态。
- 命名应表达业务意图；注释用于说明原因、约束和非显然取舍，不复述代码表面行为。
- 修改现有代码时匹配当前文件风格，不“顺便优化”相邻实现。发现无关坏味道可以说明，但不要擅自处理。
- 删除由本次修改产生的未使用 import、变量、函数和分支；除非用户要求，不清理修改前已存在的无关死代码。
- 对小型状态、进度或摘要需求，直接读取来源、选择必要字段并返回最小结果，不重建事件流或调试视图。
- 如果实现明显长于问题本身，或 200 行可以清楚地写成 50 行，应停下来简化。
- 新增函数/类 需要有明确的清晰凝练的 docstring（中文），重要的、关键的位置需要有额外的注释。
- 代码要注重可读性，要有“段落”的概念，不同语义关联的代码之间可以留一个空行，方便阅读。

自检问题：高级工程师是否会认为这段实现过度设计、过度防御、过度嵌套或过于零碎？如果会，先简化再提交。

## 代码 Review 准则

进行代码 Review 时，按以下顺序审查：

1. 首先确认代码是否能够完成基本功能，并覆盖主要使用场景；如果主路径或关键场景没有验证清楚，应优先指出。
2. 审查当前实施方案是否是上下文中的最优解，是否会增加用户或维护者的理解负担；如果存在更简洁、更容易理解但改动面更大的方案，不要直接重写，先向用户说明取舍并确认。
3. 检查是否存在过度设计、过度防御或过度嵌套：过度设计通常表现为加入无关功能；过度防御通常表现为用非预期的回退或保底掩盖设计问题；过度嵌套通常表现为 helper 过多、调用链绕、没有遵循从上到下的阅读顺序。
4. 认真评估测试脚本和测试用例的价值。对繁琐但只是在“给出靶子后评估靶子”的低价值测试，应建议清理或合并；保留能验证真实行为、关键路径和回归风险的测试。

## 开发与调试工作流 (Development & Debugging Workflow)

本项目完全通过 Docker Compose 进行管理。所有开发和调试都应在运行的容器环境中进行。使用 `docker compose up -d` 命令进行构建和启动。

**核心原则**:

1. 由于 Compose 服务 `api` / `web`（容器名 `api-dev` / `web-dev`）均配置了热重载 (hot-reloading)，本地修改代码后无需重启容器，服务会自动更新。应该先检查项目是否已经在后台启动（`docker ps`），查看日志（`docker logs api-dev --tail 100`）具体的可以阅读 [docker-compose.yml](docker-compose.yml).
2. 开发完成之后必须按改动范围进行 检查 -> 测试 -> Lint：相关单元测试必跑；涉及接口时跑集成测试；涉及关键主链路时补跑端到端测试。测试脚本不完善时应完善脚本。
3. 测试规范务必遵守 [testing-guidelines.md](docs/develop-guides/testing-guidelines.md) 中的规范，测试脚本务必放在 backend/test/unit、backend/test/integration 或 backend/test/e2e 对应目录下，并且在提交前确保测试通过。

### 需求沟通规范

在沟通需求的时候，当需求不明确的时候，需要主动挖掘需求细节，对齐需求的验收标准，明确需求的优先级和范围，避免模糊需求导致的过度设计和不必要的工作。

- 需求/修改 明确之后，如果改动较大，则需要在 docs/vibe 目录下创建一个包含日期的文档，记录需求的细节和验收标准
- 该需求文档中，还应该包括本次任务的目标以及 checklist（简要）

### 前端开发规范

- 使用 pnpm 管理
- API 接口规范：所有的 API 接口都应该定义在 web/src/apis 下面
- Icon 应该优先从 lucide-vue-next （推荐，但是需要注意尺寸）
- 样式使用 less，非特殊情况必须使用 [base.css](web/src/assets/css/base.css) 中的颜色变量
- UI 设计规范详见 [design](docs/develop-guides/design.md)

### 后端开发规范

```bash
# 代码检查和格式化
make format        # 格式化代码
```

注意：

- Python 代码要符合 pythonic 风格
- 尽量使用较新的语法，避免使用旧版本的语法（版本兼容到 3.12+）
- 更新 [changelog.md](docs/develop-guides/changelog.md) 文档记录本次修改，多个类似的功能更新已经补充在一起
- 开发完成后务必在 docker 中进行测试，可以读取 .env 获取管理员账户和密码；敏感值仅用于本地测试命令，不要输出到回复、日志摘录、测试文件或文档中

**其他**：

- 如果需要新建说明文档（仅开发者可见，非必要不创建），则保存在 `docs/vibe` 文件夹下面
- 代码更新后要检查文档部分是否有需要更新的地方，文档的目录定义在 `docs/.vitepress/config.mts` 中
- 如果新增面向用户的正式文档，除了补正文档内容外，还需要同步更新 `docs/.vitepress/config.mts` 的导航；Langfuse 集成说明归档在 `docs/agents` 分组下维护，并同步更新 `docs/develop-guides/changelog.md`

## 提交规范

1. 参考 [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) 规范编写提交信息。
2. 使用中文提交信息，标题简洁明了，描述具体改动内容和原因。
3. 创建 PR 必须参考 [contributing.md](docs/develop-guides/contributing.md) 以及 PR 模板[PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)，并在提交前完成其中的检查项。
