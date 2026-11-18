# Backend 约定

本目录承载 FastAPI 适配层、业务服务、repositories、Agent runtime 与持久化实现。先阅读根 [AGENTS.md](../AGENTS.md)；架构与开发指南按根文件的任务范围加载。

## 边界与所有权

- `yuxi/api/routers` 只处理 HTTP 模型、认证依赖、状态码和响应装配；跨 repository 的用例进入 对应 `yuxi/modules/<业务域>/services`。
- Public API（`api/routers/public_v1`）只承接用户明确要求的对外能力；未经明确要求，不得新增 Public 路由或扩展其协议。Web 产品功能放在对应业务路由，不能因已有 Public Session 链路就默认加入 Public。
- PostgreSQL 是 Thread、Turn、Input、Receipt、Run、Message、权限和业务终态的 Owner；Redis/ARQ 是投递与短期事件平面。
- 写入事实、提交事务、发布队列/事件的顺序必须显式；通知不能早于 owning transaction 的 commit point。
- 跨 repository 用例只有一个事务 Owner。需要经 HTTP 返回的一次性 secret 必须可由幂等请求安全重放，不能先不可逆消费再祈望响应送达。凭据撤销必须保留足以阻止同一幂等请求复活 secret 的 tombstone。
- parser、HTTP、模型/tool JSON、持久化、worker、process、wire 和用户路径是运行时校验边界；已由 Python 类型和同进程调用保证的内部值不重复 hostile validation。
- 核心启动依赖失败要阻止 readiness；可选集成降级要记录组件名、失败类型与当前能力。
- AgentRun 的状态转换、lease、输出和终态投影由 repository/service 统一维护，调用方不得直接拼装并行真相。

## 实现

- 修改 Python 排版时遵循[贡献指南的后端约定](../docs/develop-guides/contributing.md#后端)，核对调用换行、语义空行与必要注释。Ruff 配置由本目录 `pyproject.toml` 拥有。
- Python 使用 3.12+ 语法。保持主流程线性，优先早返回和清晰主路径，避免细碎 helper、静默 fallback、一次性抽象、不必要的嵌套和多层调用链。
- 推导式与三元表达式只承载简单的映射、过滤和转换；嵌套遍历、多步转换或大段对象构造改用显式循环和条件语句，不机械展开清晰的惯用表达式。
- 本次新增或实质修改的代码遵循向下规则：同一模块或类内，公开、高层方法按业务流程排列在上，相关定义相邻，专用实现细节在后；旧代码布局不豁免新改动。可调整直接相关定义的位置，不重排整个文件；基类、装饰器、默认值和模块初始化等定义时依赖优先满足 Python 执行顺序。Reviewer 核对局部阅读顺序。
- 拆函数用于明确复用、隔离副作用或实质降低认知负担；实现细节遮蔽主流程时，即使只调用一次也可提取。仅转发参数或包装表达式且不增加语义的 helper 通常内联。
- 常量和具名值放在最小合理作用域：跨函数复用、协议标识或配置约束用模块级常量；仅服务单个函数的局部规则留在函数内。
- 命名表达业务意图。不导入其他模块下划线开头的私有标识，确有共享需求时改为公开命名。
- 删除本次修改产生的未使用 import、变量、函数和分支，并在本次修改范围内收敛重复逻辑；不清理修改前已存在的无关死代码，发现坏味道只说明不擅自处理。
- 重构保持业务行为、API 契约、异常处理与异步并发语义不变，以相关测试验证后交付。
- 小型状态、进度或摘要需求直接读取来源并返回最小结果，不重建事件流或调试视图。
- 新增函数/类使用简洁中文 docstring。不同语义的代码段之间留空行保持可读。异常只在当前层能增加稳定语义、执行清理或决定策略时捕获。
- Schema 演进必须幂等、可在现有数据上执行，并有真实 PostgreSQL 测试；不可逆操作明确数据影响。

## 验证

```bash
docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"
(cd backend && uv run ruff check yuxi)
(cd backend && uv run ruff format yuxi --check)
(cd backend && uv run ruff check --select I yuxi)
```

以上三条 ruff 命令各自在 `backend` 工作目录执行，可从仓库根目录连续运行，检查范围与 `ruff.yml` 一致；后端 ruff 属于 `dev` 依赖组，不在只安装 `test` 组的 API 镜像内运行。完整分批、integration/E2E 命令与容器跳过边界见[测试规范](../docs/develop-guides/testing-guidelines.md#常用命令)。

并发、事务、锁、lease、schema 与 PostgreSQL 专属语义必须在真实 PostgreSQL 上验证；API 行为通过真实 HTTP integration 证明；关键 Run/worker/文件副作用通过 E2E 证明。
