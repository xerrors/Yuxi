# 智能体配置 auth 仅限制修改

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/context.py

## 问题

管理员保存的最大执行步数等参数在普通用户读取和运行时被角色过滤，运行回退默认值。

## 决策

`metadata.auth` 仅控制配置写入与可编辑字段列表。已获智能体读取权限的用户可以读取已保存字段；运行规范化保留这些参数，资源列表仍按发起用户的访问权限取交集。配置不承载凭据，智能体对象权限与资源授权不变。不增加前端只读表单或配置迁移。

## 替代方案

仅在运行时保留字段仍使读取结果与实际执行不同；移除字段的 auth 会放开普通用户修改权限。采用读写分离。

## 后果

auth 不再提供保密语义，新增 Context 字段不得把凭据放在此配置中。工具审批默认模式也按保存值生效；单次运行显式覆盖沿用现有契约。已固化的 Run 配置不回写，新 Run 使用保存值。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 普通用户读取并运行管理员参数 | 读取或规范化丢弃参数 | context.py、agent_router.py | context auth unit、真实 HTTP integration、Run E2E | 修复前运行规范化测试因参数缺失失败；E2E 限制为 1 时实际递归失败、改为 42 后完成 | Passed |
| 普通用户不能修改受限字段 | 写入绕过 auth | agent_config_service.py | HTTP 保存后回读 PG | 提交不同受限值仍保留管理员值 | Passed |
| 资源访问保持用户范围 | 参数读取扩大资源权限 | normalize_agent_context_config | 资源授权 integration | 不可见 Skill 不能加入配置 | Passed |

实际执行：

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：1934 passed、53 skipped。默认依赖同步因容器安装目录权限失败，使用已有依赖执行。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_agent_config_resource_authorization.py -q`：2 passed，真实 HTTP 与独立 PostgreSQL 连接回读。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_deterministic_agent_path_e2e.py -k standard_user_run_uses_admin_execution_limit -q`：1 passed，运行仓库确定性 replay 服务，经过真实 API、worker 和 LangGraph，核对终态、结果和持久运行清单；不证明外部模型供应商行为。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，后者 62 tests。
- `cd docs && pnpm run build`：通过。

实现和验证入口分别由 [Context 配置](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/context.py)、[HTTP 写入服务](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/agent_config_service.py)、[权限集成测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_agent_config_resource_authorization.py) 和 [确定性运行 E2E](https://github.com/xerrors/Yuxi/blob/main/backend/test/e2e/test_deterministic_agent_path_e2e.py) 拥有。配置使用说明见[配置智能体](../../../agents/agents-config.md)。
