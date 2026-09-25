# 共享智能体资源选择的保存边界

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/repositories/agent_repository.py

## 问题

共享智能体保存完整期望配置，委托管理员只能访问其中部分资源。编辑页按当前候选项过滤后整体保存，会删除不可见的既有选择，影响创建者后续运行。xhome #61 要求委托管理员保存无关配置时保留完整选择，运行时继续只使用当前操作者可访问的资源。

## 决策

保存接口将 `config_json.context` 作为字段补丁处理，省略字段保留原值。`agent_config_service` 按 Context Schema 和用户角色过滤可写字段，并复用运行时资源选项解析访问范围；运行时资源字段集合由 `yuxi.agents.context` 导出，保存边界额外处理预加载 Skill。创建和更新均经过资源校验，个人 Skill 自动启用入口只提交对应字段补丁。

`AgentRepository` 在 PostgreSQL 行锁内读取最新配置并合并。非空列表允许增删可见项，保留旧的不可见引用，拒绝新增无权访问的引用。仍保留的旧引用维持原相对顺序，新选择按请求顺序追加，避免改变 Skill 和预加载说明的加载顺序。

显式 `null`、空列表表示整体切换该字段的资源策略。工具、知识库和 Skill 的空列表表示禁用；子智能体保留空列表表示全部可访问的兼容语义。MCP 缺省与空列表的运行语义由[MCP 显式选择决策](../implemented/2026-09-25-mcp-explicit-opt-in.md)维护。前端普通列表编辑保留隐藏引用，取消最后一个可见项不会自动变成清空全部；清空操作显式移除全部引用。子智能体对应操作显示为“使用全部”，空列表与 null 均展示全部可访问项。

编辑页只提交变化的配置字段，保存后采用后端返回的合并配置建立基线。运行期继续计算期望选择与操作者可访问资源的交集，运行归一化不改写持久配置。用户操作参考由[配置智能体](../../../agents/agents-config.md#资源选择语义)维护。

## 替代方案

仅删除前端过滤无法保护直接 API 调用；仅由前端拼回隐藏引用无法拥有访问权限和最新持久状态。为每个资源增加独立增删 API 会扩大协议和维护范围，现有保存接口通过省略字段、非空列表与显式策略值表达这些操作。

## 后果

不可见的失效引用会继续保留，拥有相应访问权限的管理者可以移除可见选择，显式策略切换可以整体清空引用。字段补丁和行锁保护并发更新的最新隐藏引用；同一可见字段的并发编辑仍按最后提交的补丁生效，不引入配置版本协议。空列表与 null 的差异继续由各资源字段契约拥有，不统一改变子智能体兼容行为。

## 验证

| 验收主张 | 语义 Owner | 直接证据 | 负向案例 | 结果 |
|---|---|---|---|---|
| A 的 10 项选择在 B 只能访问其中 5 项时仍完整保存 | AgentRepository | 真实 HTTP 集成后独立 PostgreSQL 回读 | 独立进程恢复旧整体覆盖，隐藏保留断言失败 | Passed |
| 可见增删保留交错资源顺序，新增无权引用被拒绝 | 配置保存服务与 repository | 资源合并 unit、创建与更新 HTTP 422 后数据库回读 | 恢复可见项统一前置的算法，交错顺序断言失败 | Passed |
| 并发合并使用最新持久配置 | AgentRepository 行锁 | PostgreSQL 观察到实际锁等待后提交并发变更，再回读配置 | 旧配置缺少并发新增的隐藏引用 | Passed |
| B 的运行配置只包含交集，数据库保留完整期望选择 | normalize_agent_context_config | 使用真实数据库用户与资源归一化，交集为 5，持久列表为 10 | 无权资源进入有效配置或持久列表收缩 | Passed |
| 名称与模型保存不回写未修改资源，明确清空与全部策略 | AgentEditModal、配置表单与 store | 浏览器捕获真实 PUT，随后 GET 与 PostgreSQL 回读；前端 unit | 旧 store 整体提交在变动字段断言处失败 | Passed |

相关后端回归位于 `test/unit/repositories/test_agent_repository.py`、`test/unit/services/test_agent_config_service.py`、`test/unit/toolkits/test_install_skill.py` 和 `test/integration/api/test_agent_config_resource_authorization.py`。前端回归位于 `web/test/unit/agentConfigSave.test.js` 与 `web/test/unit/agentConfigUtils.test.js`。

真实 HTTP 集成测试在独立 Compose 槽位中完成，1 passed。最终简化后，在 main 开发环境运行 `docker compose exec -u 0 -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q -o faulthandler_timeout=30`，1782 passed、50 skipped。默认用户的标准 `uv run --group test` 命令因容器内 lock 文件不可写而失败，使用现有依赖完成验证，没有修改依赖锁文件。默认用户的一次完整 unit 和 integration 停滞后被中断，不计为通过；以上结果来自后续串行完成的运行。

`docker compose exec web pnpm run lint:check`、`pnpm run test:unit`（269 passed）和 `pnpm run build` 通过；工程契约检查及其 61 项单元测试、Ruff 0.16.4 对变动 Python 文件的检查与格式检查、相对链接检查、`pnpm --dir docs run build` 和 `git diff --check` 通过。浏览器验证覆盖浅深色和 1440、1024、768、375 像素宽度；375 像素下编辑弹窗无横向溢出，底层管理页已有页头溢出不在本次范围。

本次没有执行真实 worker E2E，运行资源交集证据止于使用真实身份和数据库的归一化入口，不将其表述为完整模型调用验证。
