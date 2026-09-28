# Backend 约定

本目录承载 FastAPI 适配层、业务服务、repositories、Agent runtime 与持久化实现。先阅读根 [AGENTS.md](../AGENTS.md) 和 [ARCHITECTURE.md](../ARCHITECTURE.md)。

## 边界与所有权

- `server/routers` 只处理 HTTP 模型、认证依赖、状态码和响应装配；跨 repository 的用例进入 `package/yuxi/services`。
- PostgreSQL 是 Request、Run、Message、权限和业务终态的 Owner；Redis/ARQ 是投递与短期事件平面。
- 写入事实、提交事务、发布队列/事件的顺序必须显式；通知不能早于 owning transaction 的 commit point。
- 跨 repository 用例只有一个事务 Owner；需要经 HTTP 返回的一次性 secret 必须可由幂等请求安全重放，不能先不可逆消费再祈望响应送达。凭据撤销必须保留足以阻止同一幂等请求复活 secret 的 tombstone。
- parser、HTTP、模型/tool JSON、持久化、worker、process、wire 和用户路径是运行时校验边界；已由 Python 类型和同进程调用保证的内部值不重复 hostile validation。
- 核心启动依赖失败要阻止 readiness；可选集成降级要记录组件名、失败类型与当前能力。
- AgentRun 的状态转换、lease、输出和终态投影由 repository/service 统一维护，调用方不得直接拼装并行真相。

## 实现

- Python 使用 3.12+ 语法；保持主流程线性，优先早返回和清晰主路径，避免细碎 helper、静默 fallback、一次性抽象、不必要的嵌套和多层调用链。
- 本次新增或实质修改的代码遵循向下规则：同一模块或类内，公开、高层方法在上，专用实现细节在后；旧代码布局不豁免新改动。可调整直接相关定义的位置，不重排整个文件；基类、装饰器、默认值和模块初始化等定义时依赖优先满足 Python 执行顺序。Reviewer 核对局部阅读顺序。
- 拆函数用于明确复用、隔离副作用或实质降低认知负担；实现细节遮蔽主流程时，即使只调用一次也可提取。仅转发参数或包装表达式且不增加语义的 helper 通常内联。
- 常量和具名值放在最小合理作用域：跨函数复用、协议标识或配置约束用模块级常量；仅服务单个函数的局部规则留在函数内。
- 命名表达业务意图；不导入其他模块下划线开头的私有标识，确有共享需求时改为公开命名。
- 删除本次修改产生的未使用 import、变量、函数和分支；不清理修改前已存在的无关死代码，发现坏味道只说明不擅自处理。
- 小型状态、进度或摘要需求直接读取来源并返回最小结果，不重建事件流或调试视图。
- 新增函数/类使用简洁中文 docstring；不同语义的代码段之间留空行保持可读。异常只在当前层能增加稳定语义、执行清理或决定策略时捕获。
- Schema 演进必须幂等、可在现有数据上执行，并有真实 PostgreSQL 测试；不可逆操作明确数据影响。

## 验证

```bash
docker compose exec api uv run --group test pytest test/unit -m "not slow"
docker compose exec api uv run --group test pytest test/integration
docker compose exec api uv run --group test pytest test/e2e -m e2e
docker compose exec api uv run ruff check package
docker compose exec api uv run ruff format package --check
```

并发、事务、锁、lease、schema 与 PostgreSQL 专属语义必须在真实 PostgreSQL 上验证；API 行为通过真实 HTTP integration 证明；关键 Run/worker/文件副作用通过 E2E 证明。
