
# 项目目录结构 (Project Overview)

Yuxi 是一个基于大模型的智能知识库与知识图谱智能体开发平台，融合了 RAG 技术与知识图谱技术，基于 LangGraph v1 + Vue.js + FastAPI + LightRAG 架构构建。项目完全通过 Docker Compose 进行管理，支持热重载开发。

架构代码地图见 [ARCHITECTURE.md](ARCHITECTURE.md)。修改不熟悉的模块前，先阅读其中的后端、前端、运行链路和架构不变量说明，再用符号搜索定位具体实现；该文档只维护相对稳定的系统边界，不替代细节文档或源码注释。

## 开发准则

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

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

## 开发与调试工作流 (Development & Debugging Workflow)

本项目完全通过 Docker Compose 进行管理。所有开发和调试都应在运行的容器环境中进行。使用 `docker compose up -d` 命令进行构建和启动。

**核心原则**:

1. 由于 api-dev 和 web-dev 服务均配置了热重载 (hot-reloading)，本地修改代码后无需重启容器，服务会自动更新。应该先检查项目是否已经在后台启动（`docker ps`），查看日志（`docker logs api-dev --tail 100`）具体的可以阅读 [docker-compose.yml](docker-compose.yml).
2. 开发完成之后必须进行 检查 -> 测试 -> Lint，以及端到端测试，测试脚本不完善时应完善脚本。
3. 测试规范务必遵守 [testing-guidelines.md](docs/develop-guides/testing-guidelines.md) 中的规范，测试脚本务必放在 backend/test 目录下，并且在提交前确保测试通过。

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
- 更新 [roadmap.md](docs/develop-guides/roadmap.md) 文档记录本次修改，多个类似的功能更新已经补充在一起
- 开发完成后务必在 docker 中进行测试，可以读取 .env 获取管理员账户和密码
- 不允许把代码写得稀碎：不要为简单线性逻辑拆出一堆细碎 helper；优先写成职责清晰、结构完整、可一眼读懂的实现。
- 拆函数必须服务于明确的复用、隔离副作用或降低认知负担；如果拆分后调用链更绕、上下文更分散，就应合并回更直接的实现。

**其他**：

- 如果需要新建说明文档（仅开发者可见，非必要不创建），则保存在 `docs/vibe` 文件夹下面
- 代码更新后要检查文档部分是否有需要更新的地方，文档的目录定义在 `docs/.vitepress/config.mts` 中
- 如果新增面向用户的正式文档，除了补正文档内容外，还需要同步更新 `docs/.vitepress/config.mts` 的导航；Langfuse 集成说明归档在 `docs/agents` 分组下维护，并同步更新 `docs/develop-guides/roadmap.md`

## 提交规范

1. 参考 [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) 规范编写提交信息。
2. 使用中文提交信息，标题简洁明了，描述具体改动内容和原因。
