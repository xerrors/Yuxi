---
change: agent-schedule-toolkit
design-doc: docs/superpowers/specs/2026-08-03-agent-schedule-toolkit-design.md
base-ref: 78910d92e3c61ca7f845f8574ea4297e5fc84bb9
---

# agent-schedule-toolkit 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 agent 运行时补齐 schedule 管理工具集（7 个 LangGraph `@tool`），并把"按用户隔离"下沉到仓储层；同步修复 `create_schedule_route` / `update_schedule_route` 中跨用户绑定 agent 的越权漏洞。

**Architecture:** 复用 `kbs/tools.py:23-72` 的 `@tool(args_schema=...)` + `ToolRuntime` 范式；`ScheduleRepository` 新增 4 个 owner-aware 方法（保留原方法给 ARQ worker）；`schedule_router.py` 全部切换为 owner-aware 方法并补 `agent_config_id` 归属校验；`toolkits/__init__.py` 加 `from . import schedules` 触发装饰器注册。

**Tech Stack:** FastAPI、SQLAlchemy 2.x async、Pydantic v2、LangChain `@tool`、LangGraph `ToolRuntime`、PostgreSQL（无 schema 迁移）。

## Global Constraints

本计划在执行时必须遵守以下项目级约束（来源：`AGENTS.md`、`docs/develop-guides/testing-guidelines.md`、Design Doc）：

- 工具内部 Pydantic `*Input` **不包含** `user_id` 字段（即使 LLM 传入也忽略，Design Doc Decision 2）。
- 所有工具错误一律返回可读中文字符串，不向 LLM 抛未捕获异常（Design Doc Error Handling 表）。
- HTTP 路由越权失败时返回 `HTTPException(403)`；工具失败时返回 `"无权使用该 agent"` / `"未找到该任务"` 等。
- schedule 不存在 / 不属于当前用户时**不区分**错误消息（避免泄露存在性）。
- Python 3.12+ 语法；不写碎片化 helper；遵循 `make format` 输出。
- 测试分层严格遵守 `testing-guidelines.md`：
  - 仓储层方法 → `backend/test/test_schedule_repository.py`（`pytest.mark.unit`）
  - 工具层 → `backend/test/agent_scheduled/test_agent_schedule_tools.py`（`pytest.mark.unit`）
  - 路由层 → 扩展 `backend/test/test_schedule_router.py`（`pytest.mark.integration`）
- 提交信息遵循 Conventional Commits，使用中文标题。
- Docker 环境下执行测试：`docker compose exec api uv run --group test pytest <path>`。

---

## File Structure

执行本计划前先确认以下文件地图（Design Doc Components 节对应）：

| 文件 | 角色 |
|---|---|
| `backend/package/yuxi/repositories/schedule_repository.py` | 新增 4 个 owner-aware 方法（`get_by_id_for_user` / `update_for_user` / `delete_for_user` / `list_logs_for_user`） |
| `backend/package/yuxi/agents/toolkits/schedules/__init__.py` | **新建**；空包，触发 `tools.py` 装饰器执行 |
| `backend/package/yuxi/agents/toolkits/schedules/tools.py` | **新建**；7 个 `@tool` + 7 个 Pydantic schema + 4 个内部 helper |
| `backend/package/yuxi/agents/toolkits/__init__.py:3` | 修改：在 `from . import buildin, debug, mysql` 后加 `, schedules` |
| `backend/server/routers/schedule_router.py` | 修改：所有 schedule 读写改 owner-aware；`create_schedule_route` / `update_schedule_route` 加 agent_config 归属校验 |
| `backend/test/test_schedule_repository.py` | **新建**；4 个 owner-aware 方法测试（每个普通 + admin 共 8 case） |
| `backend/test/agent_scheduled/test_agent_schedule_tools.py` | **新建**；7 工具 happy + 隔离 + admin + agent 归属 + 参数忽略 + 缺 user_id |
| `backend/test/test_schedule_router.py` | **新建或扩展**：补充 agent_config 归属校验 2 case + admin 兼容 1 case |
| `docs/develop-guides/roadmap.md` | 修改：记录本次新增 `agent-schedule-tools` 能力 + `scheduled-runs` 隔离契约加固 |
| `docs/agents/agent-schedule-tools.md` | **新建**；面向用户的工具说明（VitePress `agents` 分组） |
| `docs/.vitepress/config.mts` | 修改：在 `agents` 导航中补充入口 |

---

## 任务依赖图

```
Task 1 (仓储 owner-aware 方法 + 单测)
    ├── Task 2 (toolkit scaffold + helpers + 注册)         ← 可与 Task 1 并行
    │       ├── Task 3 (read 工具: list_my_schedules, get_schedule)
    │       ├── Task 4 (write 工具: create_schedule, update_schedule)
    │       ├── Task 5 (mutation 工具: delete_schedule, list_schedule_logs)
    │       └── Task 6 (trigger 工具: trigger_schedule)
    └── Task 7 (HTTP 路由修复 + 集成测试)                    ← 依赖 Task 1
            └── Task 8 (docs + lint + 端到端冒烟)            ← 依赖所有前置
```

- **Task 1 与 Task 2 可并行**（不同文件，无运行期依赖）。
- **Task 3–6 必须串行**（同文件不同函数，但共享 `pg_manager` fixture 与 helper，必须在 helper 落地后才能写工具）。
- **Task 7 可与 Task 3–6 并行**（不同文件，仅依赖 Task 1 的 owner-aware 方法）。
- **Task 8 阻塞所有前置**。

---

## Task 1: ScheduleRepository 新增 4 个 owner-aware 方法（含单元测试）

**Files:**
- Modify: `backend/package/yuxi/repositories/schedule_repository.py`（在 `delete_schedule` 之后、`get_due_schedules_with_lock` 之前追加 4 个方法）
- Create: `backend/test/test_schedule_repository.py`

**Interfaces:**
- Consumes: `ScheduleDefinition` / `ScheduleLog` 模型（`backend/package/yuxi/storage/postgres/models_business.py`）、`AsyncSession`
- Produces: 4 个新方法，签名严格按 Design Doc Decision 4：
  ```python
  async def get_by_id_for_user(self, schedule_id: str, user_id: str, *, is_admin: bool = False) -> ScheduleDefinition | None
  async def update_for_user(self, schedule_id: str, user_id: str, data: dict[str, Any], *, is_admin: bool = False) -> ScheduleDefinition | None
  async def delete_for_user(self, schedule_id: str, user_id: str, *, is_admin: bool = False) -> bool
  async def list_logs_for_user(self, schedule_id: str, user_id: str, *, limit: int, offset: int, is_admin: bool = False) -> list[ScheduleLog]
  ```
- **保留** `get_by_id` / `update_schedule` / `delete_schedule` / `get_logs_by_schedule_id` / `get_due_schedules_with_lock` 全部不动（ARQ worker 依赖，Design Doc Decision 4）。

**参考决策:** Decision 4 (owner-aware 仓储方法)

- [ ] **Step 1: 写失败测试 — `get_by_id_for_user`**

在 `backend/test/test_schedule_repository.py` 新建：

```python
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.storage.postgres.models_business import ScheduleDefinition


pytestmark = pytest.mark.asyncio


async def _make_schedule(db: AsyncSession, *, schedule_id: str, user_id: str) -> ScheduleDefinition:
    sched = ScheduleDefinition(
        id=schedule_id,
        name=f"s-{schedule_id}",
        user_id=user_id,
        agent_config_id=1,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return sched


async def test_get_by_id_for_user_returns_row_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s1", user_id="u1")

    result = await repo.get_by_id_for_user("s1", "u1")

    assert result is not None
    assert result.id == "s1"


async def test_get_by_id_for_user_returns_none_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s1", user_id="u1")

    result = await repo.get_by_id_for_user("s1", "u2")

    assert result is None


async def test_get_by_id_for_user_admin_skips_owner_filter(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s1", user_id="u1")

    result = await repo.get_by_id_for_user("s1", "u2", is_admin=True)

    assert result is not None
    assert result.user_id == "u1"
```

`db_session` fixture 复用 `backend/test/storage/conftest.py` 中已有的 async session 桩（无则按 `conftest.py` 中 `pytest_asyncio` 默认写一个：使用 sqlite in-memory 或复用 `pg_manager` 测试池）。

- [ ] **Step 2: 跑测试确认失败**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_repository.py -v
```

Expected: 3 个 `AttributeError: 'ScheduleRepository' object has no attribute 'get_by_id_for_user'`。

- [ ] **Step 3: 实现 `get_by_id_for_user`**

在 `backend/package/yuxi/repositories/schedule_repository.py` 的 `delete_schedule` 方法（line 59-64）后追加：

```python
    async def get_by_id_for_user(
        self, schedule_id: str, user_id: str, *, is_admin: bool = False
    ) -> ScheduleDefinition | None:
        """按 id 取 schedule，并按 user_id 过滤；admin 跳过 owner 过滤。

        HTTP 路由和 @tool 入口请用此方法；ARQ worker 仍用 get_by_id。
        """
        stmt = select(ScheduleDefinition).where(ScheduleDefinition.id == schedule_id)
        if not is_admin:
            stmt = stmt.where(ScheduleDefinition.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_repository.py::test_get_by_id_for_user_returns_row_when_owner_matches -v
```

Expected: PASS。

- [ ] **Step 5: 写失败测试 — `update_for_user`**

在同一个测试文件追加：

```python
async def test_update_for_user_modifies_row_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s2", user_id="u1")

    result = await repo.update_for_user("s2", "u1", {"name": "renamed"})

    assert result is not None
    assert result.name == "renamed"


async def test_update_for_user_returns_none_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s2", user_id="u1")

    result = await repo.update_for_user("s2", "u2", {"name": "renamed"})

    assert result is None


async def test_update_for_user_admin_can_modify_others(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s2", user_id="u1")

    result = await repo.update_for_user("s2", "u2", {"name": "renamed"}, is_admin=True)

    assert result is not None
    assert result.name == "renamed"
```

- [ ] **Step 6: 实现 `update_for_user`**

```python
    async def update_for_user(
        self,
        schedule_id: str,
        user_id: str,
        data: dict[str, Any],
        *,
        is_admin: bool = False,
    ) -> ScheduleDefinition | None:
        """按 (id, user_id) 校验后更新；admin 跳过 owner 过滤。"""
        schedule = await self.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
        if not schedule:
            return None

        for key, value in data.items():
            if hasattr(schedule, key):
                setattr(schedule, key, value)
        schedule.updated_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(schedule)
        return schedule
```

- [ ] **Step 7: 跑测试确认通过**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_repository.py -v
```

Expected: 6 个新 test 全 PASS。

- [ ] **Step 8: 写失败测试 — `delete_for_user`**

```python
async def test_delete_for_user_removes_row_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s3", user_id="u1")

    deleted = await repo.delete_for_user("s3", "u1")

    assert deleted is True
    assert await repo.get_by_id("s3") is None


async def test_delete_for_user_returns_false_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s3", user_id="u1")

    deleted = await repo.delete_for_user("s3", "u2")

    assert deleted is False
    assert (await repo.get_by_id("s3")) is not None


async def test_delete_for_user_admin_can_remove_others(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s3", user_id="u1")

    deleted = await repo.delete_for_user("s3", "u2", is_admin=True)

    assert deleted is True
```

- [ ] **Step 9: 实现 `delete_for_user`**

```python
    async def delete_for_user(
        self, schedule_id: str, user_id: str, *, is_admin: bool = False
    ) -> bool:
        """按 (id, user_id) 校验后删除；admin 跳过 owner 过滤。"""
        schedule = await self.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
        if not schedule:
            return False
        await self.db.delete(schedule)
        await self.db.flush()
        return True
```

- [ ] **Step 10: 写失败测试 — `list_logs_for_user`**

```python
from yuxi.storage.postgres.models_business import ScheduleLog
from datetime import UTC, datetime
import uuid


async def _make_log(db: AsyncSession, *, schedule_id: str) -> ScheduleLog:
    log = ScheduleLog(
        id=str(uuid.uuid4()),
        schedule_id=schedule_id,
        run_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        status="triggered",
        execution_status="pending",
        trigger_delay_ms=0,
        created_at=datetime.now(UTC),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def test_list_logs_for_user_returns_logs_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s4", user_id="u1")
    await _make_log(db_session, schedule_id="s4")

    logs = await repo.list_logs_for_user("s4", "u1", limit=20, offset=0)

    assert len(logs) == 1


async def test_list_logs_for_user_returns_empty_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s4", user_id="u1")
    await _make_log(db_session, schedule_id="s4")

    logs = await repo.list_logs_for_user("s4", "u2", limit=20, offset=0)

    assert logs == []


async def test_list_logs_for_user_admin_can_read_others(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s4", user_id="u1")
    await _make_log(db_session, schedule_id="s4")

    logs = await repo.list_logs_for_user("s4", "u2", limit=20, offset=0, is_admin=True)

    assert len(logs) == 1
```

- [ ] **Step 11: 实现 `list_logs_for_user`**

```python
    async def list_logs_for_user(
        self,
        schedule_id: str,
        user_id: str,
        *,
        limit: int,
        offset: int,
        is_admin: bool = False,
    ) -> list[ScheduleLog]:
        """校验 schedule 归属后返回其执行日志；admin 跳过 owner 过滤。"""
        owner_ok = await self.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
        if not owner_ok:
            return []

        stmt = (
            select(ScheduleLog)
            .where(ScheduleLog.schedule_id == schedule_id)
            .order_by(ScheduleLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
```

- [ ] **Step 12: 跑全部新测试**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_repository.py -v
```

Expected: 12 个 test 全 PASS（3 + 3 + 3 + 3）。

- [ ] **Step 13: 提交**

```bash
git add backend/package/yuxi/repositories/schedule_repository.py backend/test/test_schedule_repository.py
git commit -m "feat(schedule): 新增 ScheduleRepository owner-aware 方法"
```

---

## Task 2: toolkit scaffold + helpers + 注册

**Files:**
- Create: `backend/package/yuxi/agents/toolkits/schedules/__init__.py`
- Create: `backend/package/yuxi/agents/toolkits/schedules/tools.py`（仅 helpers，不含 `@tool` 函数）
- Modify: `backend/package/yuxi/agents/toolkits/__init__.py:3`

**Interfaces:**
- 4 个 helper（Task 3-6 复用）：
  - `_resolve_user(runtime) -> str | None` — 从 `runtime.context.user_id` 拿当前用户
  - `_is_admin(runtime, db_session) -> bool` — 优先 `runtime.context.is_admin`；缺失则查 `users.role`
  - `_json_or_error(obj, err) -> str` — 成功 `json.dumps(..., ensure_ascii=False, default=str)`；失败返回 err
  - `_check_agent_ownership(db_session, agent_config_id, user_id, is_admin) -> str | None` — 返回 `None` 表示通过；返回字符串表示错误消息
- 工具模块 `toolkits/__init__.py` 加 `from . import schedules`（Design Doc Decision 1）。

**参考决策:** Decision 1（工具实现位置）、Decision 2（用户上下文来源）、Decision 3（数据访问）、Open Question 1（`is_admin` 字段）。

- [ ] **Step 1: 验证 `BaseContext` 是否带 `is_admin` 字段**

```bash
grep -n "is_admin" backend/package/yuxi/agents/context.py
```

- 若有 `is_admin: bool` 字段 → 在 `_is_admin` 中直接 `getattr(runtime.context, "is_admin", False)`。
- 若无 → 在 `_is_admin` 中 fallback 查 `users.role`（Step 3 实现细节会给出）。本仓库当前**没有** `is_admin` 字段，参考 Step 3 的 fallback 实现。

- [ ] **Step 2: 创建包骨架 `__init__.py`**

`backend/package/yuxi/agents/toolkits/schedules/__init__.py`：

```python
"""agent 运行时 schedule 管理工具子包

触发 tools.py 中 @tool 装饰器执行，使 7 个新工具被
yuxi.agents.toolkits.registry.get_all_tool_instances() 自动收集。
"""
```

- [ ] **Step 3: 创建 `tools.py` 框架 + 4 个 helpers**

`backend/package/yuxi/agents/toolkits/schedules/tools.py`：

```python
"""agent 运行时 schedule 管理工具

7 个 LangGraph @tool：
  - list_my_schedules
  - get_schedule
  - create_schedule
  - update_schedule
  - delete_schedule
  - trigger_schedule
  - list_schedule_logs

所有工具通过 runtime.context.user_id 强制 owner 隔离；
admin 通过 runtime.context.is_admin（若 BaseContext 带）或
fallback 到 user.role 判断。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.agent_config_repository import AgentConfigRepository
from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.services.schedule_manager import compute_next_run
from yuxi.services.schedule_service import ScheduleService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ScheduleDefinition, User
from yuxi.utils import logger


# ========== 内部 helpers ==========


def _resolve_user(runtime: ToolRuntime) -> str | None:
    """从 runtime.context 拿当前用户 user_id；缺失返回 None。"""
    context = getattr(runtime, "context", None)
    if context is None:
        return None
    return getattr(context, "user_id", None)


async def _is_admin(runtime: ToolRuntime, db_session: AsyncSession) -> bool:
    """判断当前用户是否为 admin。

    优先使用 runtime.context.is_admin（若 BaseContext 扩展过该字段）；
    否则通过 user_id 查 users.role 兜底。
    """
    context = getattr(runtime, "context", None)
    if context is not None:
        flag = getattr(context, "is_admin", None)
        if flag is not None:
            return bool(flag)
    user_id = _resolve_user(runtime)
    if not user_id:
        return False
    stmt = select(User.role).where(User.user_id == user_id).limit(1)
    result = await db_session.execute(stmt)
    role = result.scalar_one_or_none()
    return role in ("admin", "superadmin")


def _json_or_error(obj: Any, err: str) -> str:
    """成功返回 JSON 字符串；obj 为空/异常时返回 err。"""
    if obj is None:
        return err
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning(f"工具结果 JSON 序列化失败: {exc}")
        return err


async def _check_agent_ownership(
    db_session: AsyncSession,
    agent_config_id: int,
    user_id: str,
    is_admin: bool,
) -> str | None:
    """校验 agent_config 归属当前用户。

    返回 None 表示通过；返回字符串为面向 LLM 的中文错误消息。
    admin 跳过校验。
    """
    if is_admin:
        return None
    config = await AgentConfigRepository(db_session).get_by_id(agent_config_id)
    if config is None or str(config.user_id) != str(user_id):
        return "无权使用该 agent"
    return None
```

- [ ] **Step 4: 注册子模块**

修改 `backend/package/yuxi/agents/toolkits/__init__.py:3`：

```python
from . import buildin, debug, mysql, schedules
```

并在文件底部 `__all__` 列表中追加 `"schedules"`（保持 `__all__` 与 import 对齐）。

- [ ] **Step 5: 验证 import + 触发装饰器**

```bash
docker compose exec api python -c "from yuxi.agents.toolkits import get_all_tool_instances; names=[t.name for t in get_all_tool_instances()]; print([n for n in names if 'schedule' in n])"
```

Expected: 打印 `[]`（此时还没有任何 `@tool`，但 import 不报错）。如果 ImportError，检查 `__init__.py` 路径与 `pg_manager` 是否在 api-dev 容器中可访问。

- [ ] **Step 6: 提交**

```bash
git add backend/package/yuxi/agents/toolkits/schedules/ backend/package/yuxi/agents/toolkits/__init__.py
git commit -m "feat(schedule): 新建 schedules toolkit 子包与共享 helpers"
```

---

## Task 3: 实现 read 工具（`list_my_schedules` + `get_schedule`）

**Files:**
- Modify: `backend/package/yuxi/agents/toolkits/schedules/tools.py`（追加）
- Create: `backend/test/agent_scheduled/test_agent_schedule_tools.py`

**Interfaces:**
- `list_my_schedules(limit: int = 20, offset: int = 0, runtime: ToolRuntime)` — 列表当前用户 schedule（admin 看全部）
- `get_schedule(schedule_id: str, runtime: ToolRuntime)` — 取单条
- 两个 Pydantic `*Input` schema **不包含** `user_id` 字段（Design Doc Decision 2）。
- 列表常量：`LIST_DEFAULT_LIMIT = 20`，`LIST_MAX_LIMIT = 100`（Design Doc Decision 6）。

**参考决策:** Decision 2、3、6、7（admin 跳过隔离）、Decision 9（不做 cron 预校验）。

- [ ] **Step 1: 写失败测试 — `list_my_schedules` 隔离**

`backend/test/agent_scheduled/test_agent_schedule_tools.py`：

```python
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.agents.toolkits.schedules import tools
from yuxi.agents.toolkits.schedules.tools import ListMySchedulesInput


pytestmark = pytest.mark.asyncio


# ========== fake session / repo factory ==========


class _FakeRepo:
    def __init__(self, methods: dict[str, AsyncMock]):
        self._methods = methods
        for name, m in methods.items():
            setattr(self, name, m)


def _make_runtime(user_id: str | None = "u1", is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(user_id=user_id, is_admin=is_admin))


def _patch_session(monkeypatch, repo: _FakeRepo) -> None:
    @asynccontextmanager
    async def _ctx():
        session = MagicMock()
        # 同时让 _check_agent_ownership 拿到的 repo 也是 stub
        yield session

    # 直接 patch：让工具内"ScheduleRepository(session)"返回我们的 stub
    def _factory(_session):
        return repo

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", _factory)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: MagicMock())


def _call(coro_or_value):
    """工具返回 str；直接断言。"""
    return coro_or_value


# ========== list_my_schedules ==========


async def test_list_my_schedules_filters_by_current_user(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[SimpleNamespace(id="s1", user_id="u1")])})
    _patch_session(monkeypatch, repo)

    result = await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(),
        runtime=_make_runtime(user_id="u1"),
    )

    repo._methods["list_schedules"].assert_awaited_once()
    kwargs = repo._methods["list_schedules"].await_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 0
    payload = json.loads(result)
    assert payload[0]["id"] == "s1"


async def test_list_my_schedules_admin_passes_none_user_filter(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(),
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    kwargs = repo._methods["list_schedules"].await_args.kwargs
    assert kwargs["user_id"] is None


async def test_list_my_schedules_clamps_limit(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(limit=9999),
        runtime=_make_runtime(user_id="u1"),
    )

    kwargs = repo._methods["list_schedules"].await_args.kwargs
    assert kwargs["limit"] == 100  # LIST_MAX_LIMIT


async def test_list_my_schedules_returns_error_when_user_id_missing(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    result = await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(),
        runtime=_make_runtime(user_id=None),
    )

    assert result == "无法获取用户信息"
    repo._methods["list_schedules"].assert_not_awaited()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 4 个 `AttributeError`（`tools` 模块还没有 `list_my_schedules`）。

- [ ] **Step 3: 实现 `list_my_schedules`**

在 `tools.py` 末尾（`_check_agent_ownership` 之后）追加：

> **重要：** `ScheduleService` / `ScheduleDefinition` / `compute_next_run` / `ScheduleRepository` / `uuid` 都已在 tools.py 顶部 import（Task 2 Step 3），请勿在工具函数内重复 `from ... import`。

```python
# ========== list_my_schedules ==========


LIST_DEFAULT_LIMIT = 20
LIST_MAX_LIMIT = 100


class ListMySchedulesInput(BaseModel):
    """列出当前用户的定时任务；admin 看全部。"""

    limit: int = LIST_DEFAULT_LIMIT
    offset: int = 0


@tool(args_schema=ListMySchedulesInput)  # type: ignore[misc]
async def list_my_schedules(  # type: ignore[no-redef]
    args: ListMySchedulesInput,
    runtime: ToolRuntime,
) -> str:
    """列出当前用户可访问的定时任务列表（admin 看全部）。

    Args:
        args.limit: 最多返回条数（默认 20，最大 100）
        args.offset: 分页偏移

    Returns:
        JSON 数组；每条含 id/name/cron_expr/enabled/next_run_at 等。
    """
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    limit = min(max(int(args.limit), 1), LIST_MAX_LIMIT)
    offset = max(int(args.offset), 0)

    async with pg_manager.get_async_session_context() as session:
        is_admin = await _is_admin(runtime, session)
        user_filter = None if is_admin else user_id
        repo = ScheduleRepository(session)
        rows = await repo.list_schedules(user_id=user_filter, limit=limit, offset=offset)

    payload = [
        {
            "id": r.id,
            "name": r.name,
            "cron_expr": r.cron_expr,
            "timezone": r.timezone,
            "enabled": r.enabled,
            "next_run_at": r.next_run_at,
            "agent_config_id": r.agent_config_id,
        }
        for r in rows
    ]
    return _json_or_error(payload, "未找到任何定时任务")
```

> **实现注意：** 函数签名遵循 Design Decision 2：`async def list_my_schedules(args: ListMySchedulesInput, runtime: ToolRuntime) -> str`，从 `args` 取 Pydantic 入参；与 `create_schedule` / `update_schedule` 等保持一致。`ScheduleRepository` 在 tools.py 顶部已 import，不要在函数内重复 `from ... import`。

- [ ] **Step 4: 跑测试**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 4 个 list_my_schedules test 全 PASS（如果 Step 3 的 `runtime.state` 读取方式不符合 LangGraph 真实行为，先调整 Step 3 实现而非测试）。

- [ ] **Step 5: 写失败测试 — `get_schedule`**

追加：

```python
# ========== get_schedule ==========


async def test_get_schedule_returns_row_for_owner(monkeypatch) -> None:
    fake_row = SimpleNamespace(id="s9", user_id="u1", to_dict=lambda: {"id": "s9"})
    repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=fake_row)})
    _patch_session(monkeypatch, repo)

    result = await tools.get_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="s9",
        runtime=_make_runtime(user_id="u1"),
    )

    repo._methods["get_by_id_for_user"].assert_awaited_once_with("s9", "u1", is_admin=False)
    payload = json.loads(result)
    assert payload["id"] == "s9"


async def test_get_schedule_returns_friendly_error_for_other_user(monkeypatch) -> None:
    repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=None)})
    _patch_session(monkeypatch, repo)

    result = await tools.get_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="s9",
        runtime=_make_runtime(user_id="u2"),
    )

    assert result == "未找到该任务"


async def test_get_schedule_admin_can_read_others(monkeypatch) -> None:
    fake_row = SimpleNamespace(id="s9", user_id="u1", to_dict=lambda: {"id": "s9"})
    repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=fake_row)})
    _patch_session(monkeypatch, repo)

    # is_admin=True 时 _is_admin 走 runtime.context.is_admin 分支，不查 DB
    await tools.get_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="s9",
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    repo._methods["get_by_id_for_user"].assert_awaited_once_with("s9", "admin1", is_admin=True)
```

- [ ] **Step 6: 实现 `get_schedule`**

```python
# ========== get_schedule ==========


class GetScheduleInput(BaseModel):
    """获取单条定时任务详情。"""

    schedule_id: str


@tool(args_schema=GetScheduleInput)  # type: ignore[misc]
async def get_schedule(schedule_id: str, runtime: ToolRuntime) -> str:  # type: ignore[no-redef]
    """获取单条定时任务详情（按 owner 隔离）。

    Args:
        schedule_id: 任务 ID

    Returns:
        JSON 字符串；无权访问时返回"未找到该任务"。
    """
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    async with pg_manager.get_async_session_context() as session:
        is_admin = await _is_admin(runtime, session)
        repo = ScheduleRepository(session)
        row = await repo.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)

    if row is None:
        return "未找到该任务"
    return _json_or_error(row.to_dict(), "未找到该任务")
```

- [ ] **Step 7: 跑测试**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 7 个 test 全 PASS。

- [ ] **Step 8: 提交**

```bash
git add backend/package/yuxi/agents/toolkits/schedules/tools.py backend/test/agent_scheduled/test_agent_schedule_tools.py
git commit -m "feat(schedule): 新增 list_my_schedules 与 get_schedule 工具"
```

---

## Task 4: 实现 write 工具（`create_schedule` + `update_schedule`，含 agent 归属校验）

**Files:**
- Modify: `backend/package/yuxi/agents/toolkits/schedules/tools.py`（追加）
- Modify: `backend/test/agent_scheduled/test_agent_schedule_tools.py`（追加测试）

**Interfaces:**
- `create_schedule(name, description, agent_config_id, cron_expr, timezone, query, image_content, config, enabled, runtime)`
- `update_schedule(schedule_id, ..., runtime)`
- 两个 schema `*Input` 不含 `user_id`；`agent_config_id: int`（Design Doc Decision 8）。
- `_check_agent_ownership` 失败时返回中文错误（Design Doc Decision 5、Error Handling 表）。
- 工具层不预校验 cron；`ScheduleService.create_scheduled_run` 触发 `croniter` 异常时返回 "cron 表达式无效"（Design Doc Decision 9）。

**参考决策:** Decision 5、8、9、Error Handling。

- [ ] **Step 1: 写失败测试 — `create_schedule` happy + 归属校验**

追加：

```python
from datetime import UTC, datetime


# ========== create_schedule ==========


async def test_create_schedule_succeeds_when_agent_belongs_to_user(monkeypatch) -> None:
    fake_schedule = SimpleNamespace(
        id="new-1", to_dict=lambda: {"id": "new-1", "name": "demo"}
    )
    sched_repo = _FakeRepo({"create_schedule": AsyncMock(return_value=fake_schedule)})
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="u1"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    def _sched_factory(_s):
        return sched_repo

    def _agent_factory(_s):
        return agent_repo

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", _sched_factory)
    monkeypatch.setattr(tools, "AgentConfigRepository", _agent_factory)

    result = await tools.create_schedule.coroutine(  # type: ignore[attr-defined]
        name="demo",
        description=None,
        agent_config_id=42,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
        image_content=None,
        config={},
        enabled=True,
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload["id"] == "new-1"
    sched_repo._methods["create_schedule"].assert_awaited_once()


async def test_create_schedule_rejects_foreign_agent(monkeypatch) -> None:
    sched_repo = _FakeRepo({"create_schedule": AsyncMock()})
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="other_user"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.create_schedule.coroutine(  # type: ignore[attr-defined]
        name="demo",
        description=None,
        agent_config_id=42,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
        image_content=None,
        config={},
        enabled=True,
        runtime=_make_runtime(user_id="u1"),
    )

    assert result == "无权使用该 agent"
    sched_repo._methods["create_schedule"].assert_not_awaited()


async def test_create_schedule_admin_bypasses_agent_ownership(monkeypatch) -> None:
    fake_schedule = SimpleNamespace(id="new-2", to_dict=lambda: {"id": "new-2"})
    sched_repo = _FakeRepo({"create_schedule": AsyncMock(return_value=fake_schedule)})
    # admin 路径下不应调用 AgentConfigRepository
    agent_repo = _FakeRepo({"get_by_id": AsyncMock()})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    await tools.create_schedule.coroutine(  # type: ignore[attr-defined]
        name="demo",
        description=None,
        agent_config_id=42,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
        image_content=None,
        config={},
        enabled=True,
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    agent_repo._methods["get_by_id"].assert_not_awaited()
    sched_repo._methods["create_schedule"].assert_awaited_once()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 3 个新 case 全 FAIL（`tools.create_schedule` 不存在）。

- [ ] **Step 3: 实现 `create_schedule`**

在 `tools.py` 末尾追加：

> **重要：** `ScheduleService` / `ScheduleDefinition` / `compute_next_run` / `ScheduleRepository` / `uuid` 都已在 tools.py 顶部 import（Task 2 Step 3），请勿在工具函数内重复 `from ... import`。

```python
# ========== create_schedule ==========


class CreateScheduleInput(BaseModel):
    """创建一个新的定时任务。"""

    name: str
    description: str | None = None
    agent_config_id: int
    cron_expr: str
    timezone: str = "Asia/Shanghai"
    query: str
    image_content: str | None = None
    config: dict = {}
    enabled: bool = True


@tool(args_schema=CreateScheduleInput)  # type: ignore[misc]
async def create_schedule(  # type: ignore[no-redef]
    name: str,
    description: str | None,
    agent_config_id: int,
    cron_expr: str,
    timezone: str,
    query: str,
    image_content: str | None,
    config: dict,
    enabled: bool,
    runtime: ToolRuntime,
) -> str:
    """创建新的定时任务。普通用户只能绑定自己创建的 agent_config；admin 不受限。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            err = await _check_agent_ownership(session, agent_config_id, user_id, is_admin)
            if err:
                return err

            # 计算 next_run_at；cron 失败由 compute_next_run 抛
            next_run = None
            if enabled:
                try:
                    next_run = compute_next_run(cron_expr, timezone)
                except Exception as e:
                    return f"cron 表达式无效: {e}"

            schedule = ScheduleDefinition(
                id=str(uuid.uuid4()),
                name=name,
                description=description,
                user_id=str(user_id),
                agent_config_id=agent_config_id,
                cron_expr=cron_expr,
                timezone=timezone,
                query=query,
                image_content=image_content,
                config=config or {},
                enabled=enabled,
                next_run_at=next_run,
            )
            repo = ScheduleRepository(session)
            created = await repo.create_schedule(schedule)
            await session.commit()
            return _json_or_error(created.to_dict(), "创建失败")
    except Exception as e:
        logger.error(f"create_schedule 工具异常: {e}")
        return f"创建失败: {e}"
```

> **Note:** `create_schedule` 工具**不**调用 `ScheduleService.create_scheduled_run`（那只是创建一次性的 thread + run，schedule 本身的持久化走 `repo.create_schedule`）。Design Doc Decision 10 提到的 `manual_trigger_schedule` 在 `trigger_schedule` 工具中调用。本测试**不要** mock `tools.ScheduleService` —— 工具不调用它。

- [ ] **Step 4: 跑测试**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 10 个 test 全 PASS（4 + 3 + 3）。

- [ ] **Step 5: 写失败测试 — `update_schedule` happy + 归属校验**

追加：

```python
# ========== update_schedule ==========


async def test_update_schedule_rejects_foreign_agent(monkeypatch) -> None:
    sched_repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=SimpleNamespace(id="sx"))})
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="other"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.update_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        name=None,
        description=None,
        agent_config_id=99,
        cron_expr=None,
        timezone=None,
        query=None,
        image_content=None,
        config=None,
        enabled=None,
        runtime=_make_runtime(user_id="u1"),
    )

    assert result == "无权使用该 agent"
    sched_repo._methods["get_by_id_for_user"].assert_not_awaited()  # 校验在 update 前


async def test_update_schedule_succeeds_when_owner_and_agent_match(monkeypatch) -> None:
    existing = SimpleNamespace(id="sx", user_id="u1", to_dict=lambda: {"id": "sx"})
    updated = SimpleNamespace(id="sx", name="new", to_dict=lambda: {"id": "sx", "name": "new"})
    sched_repo = _FakeRepo(
        {
            "get_by_id_for_user": AsyncMock(return_value=existing),
            "update_for_user": AsyncMock(return_value=updated),
        }
    )
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="u1"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.update_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        name="new",
        description=None,
        agent_config_id=42,
        cron_expr=None,
        timezone=None,
        query=None,
        image_content=None,
        config=None,
        enabled=None,
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload["name"] == "new"
    sched_repo._methods["update_for_user"].assert_awaited_once()


async def test_update_schedule_skips_agent_check_when_agent_id_not_provided(monkeypatch) -> None:
    """update_schedule 若 agent_config_id=None，应跳过 _check_agent_ownership。"""
    existing = SimpleNamespace(id="sx", user_id="u1", to_dict=lambda: {"id": "sx"})
    updated = SimpleNamespace(id="sx", name="x", to_dict=lambda: {"id": "sx", "name": "x"})
    sched_repo = _FakeRepo(
        {
            "get_by_id_for_user": AsyncMock(return_value=existing),
            "update_for_user": AsyncMock(return_value=updated),
        }
    )
    agent_repo = _FakeRepo({"get_by_id": AsyncMock()})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.update_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        name="x",
        description=None,
        agent_config_id=None,
        cron_expr=None,
        timezone=None,
        query=None,
        image_content=None,
        config=None,
        enabled=None,
        runtime=_make_runtime(user_id="u1"),
    )

    assert json.loads(result)["name"] == "x"
    agent_repo._methods["get_by_id"].assert_not_awaited()
```

- [ ] **Step 6: 实现 `update_schedule`**

```python
# ========== update_schedule ==========


class UpdateScheduleInput(BaseModel):
    """更新定时任务字段；只更新提供的字段。"""

    schedule_id: str
    name: str | None = None
    description: str | None = None
    agent_config_id: int | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    query: str | None = None
    image_content: str | None = None
    config: dict | None = None
    enabled: bool | None = None


@tool(args_schema=UpdateScheduleInput)  # type: ignore[misc]
async def update_schedule(  # type: ignore[no-redef]
    schedule_id: str,
    name: str | None,
    description: str | None,
    agent_config_id: int | None,
    cron_expr: str | None,
    timezone: str | None,
    query: str | None,
    image_content: str | None,
    config: dict | None,
    enabled: bool | None,
    runtime: ToolRuntime,
) -> str:
    """更新定时任务；agent_config_id 必须归属当前用户（admin 跳过）。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            if agent_config_id is not None:
                err = await _check_agent_ownership(session, agent_config_id, user_id, is_admin)
                if err:
                    return err

            update_data: dict[str, Any] = {}
            if name is not None:
                update_data["name"] = name
            if description is not None:
                update_data["description"] = description
            if agent_config_id is not None:
                update_data["agent_config_id"] = agent_config_id
            if cron_expr is not None:
                update_data["cron_expr"] = cron_expr
            if timezone is not None:
                update_data["timezone"] = timezone
            if query is not None:
                update_data["query"] = query
            if image_content is not None:
                update_data["image_content"] = image_content
            if config is not None:
                update_data["config"] = config
            if enabled is not None:
                update_data["enabled"] = enabled

            # 若改了 cron/时区/启用状态，重算 next_run_at
            if enabled or "cron_expr" in update_data or "timezone" in update_data:
                final_cron = update_data.get("cron_expr")
                final_tz = update_data.get("timezone")
                final_enabled = update_data.get("enabled", enabled if enabled is not None else True)
                if final_enabled:
                    try:
                        # 需要原值兜底；这里用 None 时抛错
                        if final_cron is None or final_tz is None:
                            raise ValueError("缺少 cron 或时区")
                        update_data["next_run_at"] = compute_next_run(final_cron, final_tz)
                    except Exception as e:
                        return f"cron 表达式无效: {e}"
                else:
                    update_data["next_run_at"] = None

            repo = ScheduleRepository(session)
            updated = await repo.update_for_user(schedule_id, user_id, update_data, is_admin=is_admin)
            if updated is None:
                return "未找到该任务"
            await session.commit()
            return _json_or_error(updated.to_dict(), "更新失败")
    except Exception as e:
        logger.error(f"update_schedule 工具异常: {e}")
        return f"更新失败: {e}"
```

- [ ] **Step 7: 跑测试**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 13 个 test 全 PASS（10 + 3）。

- [ ] **Step 8: 提交**

```bash
git add backend/package/yuxi/agents/toolkits/schedules/tools.py backend/test/agent_scheduled/test_agent_schedule_tools.py
git commit -m "feat(schedule): 新增 create_schedule 与 update_schedule 工具"
```

---

## Task 5: 实现 mutation 工具（`delete_schedule` + `list_schedule_logs`）

**Files:**
- Modify: `backend/package/yuxi/agents/toolkits/schedules/tools.py`（追加）
- Modify: `backend/test/agent_scheduled/test_agent_schedule_tools.py`（追加测试）

**Interfaces:**
- `delete_schedule(schedule_id, runtime)` — 通过 `repo.delete_for_user`
- `list_schedule_logs(schedule_id, limit, offset, runtime)` — 通过 `repo.list_logs_for_user`
- 两个 schema `*Input` 不含 `user_id`；list_schedule_logs 有 `limit`/`offset`（Design Doc Decision 6）。

**参考决策:** Decision 4、6、7。

- [ ] **Step 1: 写失败测试**

追加：

```python
# ========== delete_schedule ==========


async def test_delete_schedule_succeeds_for_owner(monkeypatch) -> None:
    repo = _FakeRepo({"delete_for_user": AsyncMock(return_value=True)})
    _patch_session(monkeypatch, repo)

    result = await tools.delete_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload["deleted"] is True
    repo._methods["delete_for_user"].assert_awaited_once_with("sx", "u1", is_admin=False)


async def test_delete_schedule_returns_friendly_error_for_other_user(monkeypatch) -> None:
    repo = _FakeRepo({"delete_for_user": AsyncMock(return_value=False)})
    _patch_session(monkeypatch, repo)

    result = await tools.delete_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        runtime=_make_runtime(user_id="u2"),
    )

    assert result == "未找到该任务"


# ========== list_schedule_logs ==========


async def test_list_schedule_logs_returns_logs_for_owner(monkeypatch) -> None:
    fake_logs = [SimpleNamespace(id="l1", to_dict=lambda: {"id": "l1"})]
    repo = _FakeRepo({"list_logs_for_user": AsyncMock(return_value=fake_logs)})
    _patch_session(monkeypatch, repo)

    result = await tools.list_schedule_logs.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        limit=20,
        offset=0,
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload[0]["id"] == "l1"
    repo._methods["list_logs_for_user"].assert_awaited_once()


async def test_list_schedule_logs_returns_empty_for_other_user(monkeypatch) -> None:
    """仓储层 list_logs_for_user 在 owner 不匹配时返回 []；工具返回'未找到该任务'。"""
    repo = _FakeRepo({"list_logs_for_user": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    result = await tools.list_schedule_logs.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        limit=20,
        offset=0,
        runtime=_make_runtime(user_id="u2"),
    )

    assert result == "未找到该任务"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 4 个新 case FAIL。

- [ ] **Step 3: 实现 `delete_schedule`**

```python
# ========== delete_schedule ==========


class DeleteScheduleInput(BaseModel):
    """删除定时任务。"""

    schedule_id: str


@tool(args_schema=DeleteScheduleInput)  # type: ignore[misc]
async def delete_schedule(schedule_id: str, runtime: ToolRuntime) -> str:  # type: ignore[no-redef]
    """删除定时任务（按 owner 隔离）。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            repo = ScheduleRepository(session)
            ok = await repo.delete_for_user(schedule_id, user_id, is_admin=is_admin)
        if not ok:
            return "未找到该任务"
        return _json_or_error({"deleted": True, "schedule_id": schedule_id}, "删除失败")
    except Exception as e:
        logger.error(f"delete_schedule 工具异常: {e}")
        return f"删除失败: {e}"
```

- [ ] **Step 4: 实现 `list_schedule_logs`**

```python
# ========== list_schedule_logs ==========


class ListScheduleLogsInput(BaseModel):
    """列出指定定时任务的执行日志。"""

    schedule_id: str
    limit: int = LIST_DEFAULT_LIMIT
    offset: int = 0


@tool(args_schema=ListScheduleLogsInput)  # type: ignore[misc]
async def list_schedule_logs(  # type: ignore[no-redef]
    schedule_id: str,
    limit: int,
    offset: int,
    runtime: ToolRuntime,
) -> str:
    """列出指定任务的执行日志（按 owner 隔离；admin 可看全部）。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    limit = min(max(int(limit), 1), LIST_MAX_LIMIT)
    offset = max(int(offset), 0)

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            repo = ScheduleRepository(session)
            logs = await repo.list_logs_for_user(
                schedule_id, user_id, limit=limit, offset=offset, is_admin=is_admin
            )
        if not logs:
            return "未找到该任务"
        return _json_or_error([log.to_dict() for log in logs], "未找到该任务")
    except Exception as e:
        logger.error(f"list_schedule_logs 工具异常: {e}")
        return f"查询失败: {e}"
```

- [ ] **Step 5: 跑测试**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 17 个 test 全 PASS（13 + 4）。

- [ ] **Step 6: 提交**

```bash
git add backend/package/yuxi/agents/toolkits/schedules/tools.py backend/test/agent_scheduled/test_agent_schedule_tools.py
git commit -m "feat(schedule): 新增 delete_schedule 与 list_schedule_logs 工具"
```

---

## Task 6: 实现 `trigger_schedule` 工具

**Files:**
- Modify: `backend/package/yuxi/agents/toolkits/schedules/tools.py`（追加）
- Modify: `backend/test/agent_scheduled/test_agent_schedule_tools.py`（追加测试）

**Interfaces:**
- `trigger_schedule(schedule_id, runtime)` — 复用 `ScheduleService.manual_trigger_schedule`（Design Doc Decision 10）
- `*Input` 不含 `user_id`

**参考决策:** Decision 4、7、10。

- [ ] **Step 1: 写失败测试**

追加：

```python
# ========== trigger_schedule ==========


async def test_trigger_schedule_succeeds_for_owner(monkeypatch) -> None:
    sched_repo = _FakeRepo(
        {"get_by_id_for_user": AsyncMock(return_value=SimpleNamespace(id="sx"))}
    )

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)

    class _FakeService:
        async def manual_trigger_schedule(self, *, schedule, db):
            return ("thread-1", "run-1")

    monkeypatch.setattr(tools, "ScheduleService", _FakeService)

    result = await tools.trigger_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload["thread_id"] == "thread-1"
    assert payload["run_id"] == "run-1"


async def test_trigger_schedule_returns_friendly_error_for_other_user(monkeypatch) -> None:
    sched_repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=None)})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)

    result = await tools.trigger_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        runtime=_make_runtime(user_id="u2"),
    )

    assert result == "未找到该任务"


async def test_trigger_schedule_admin_can_trigger_others(monkeypatch) -> None:
    sched_repo = _FakeRepo(
        {"get_by_id_for_user": AsyncMock(return_value=SimpleNamespace(id="sx"))}
    )

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)

    class _FakeService:
        async def manual_trigger_schedule(self, *, schedule, db):
            return ("t1", "r1")

    monkeypatch.setattr(tools, "ScheduleService", _FakeService)

    await tools.trigger_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    sched_repo._methods["get_by_id_for_user"].assert_awaited_once_with("sx", "admin1", is_admin=True)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 3 个新 case FAIL（`tools.trigger_schedule` 不存在）。

- [ ] **Step 3: 实现 `trigger_schedule`**

```python
# ========== trigger_schedule ==========


class TriggerScheduleInput(BaseModel):
    """立即触发一次定时任务。"""

    schedule_id: str


@tool(args_schema=TriggerScheduleInput)  # type: ignore[misc]
async def trigger_schedule(schedule_id: str, runtime: ToolRuntime) -> str:  # type: ignore[no-redef]
    """立即触发一次定时任务；不影响原 cron 周期。

    即使 schedule 处于 disabled 状态也可触发（与现有 manual_trigger_schedule 行为一致）。
    """
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            repo = ScheduleRepository(session)
            schedule = await repo.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
            if schedule is None:
                return "未找到该任务"

            service = ScheduleService()
            thread_id, run_id = await service.manual_trigger_schedule(schedule=schedule, db=session)
            await session.commit()
        return _json_or_error(
            {"thread_id": thread_id, "run_id": run_id, "schedule_id": schedule_id}, "触发失败"
        )
    except Exception as e:
        logger.error(f"trigger_schedule 工具异常: {e}")
        return f"触发失败: {e}"
```

- [ ] **Step 4: 跑测试**

```bash
docker compose exec api uv run --group test pytest backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 20 个 test 全 PASS（17 + 3）。

- [ ] **Step 5: 验证 7 个工具已注册到 `get_all_tool_instances()`**

```bash
docker compose exec api python -c "
from yuxi.agents.toolkits import get_all_tool_instances
names = sorted([t.name for t in get_all_tool_instances()])
schedule_tools = [n for n in names if n in {
    'list_my_schedules', 'get_schedule', 'create_schedule', 'update_schedule',
    'delete_schedule', 'trigger_schedule', 'list_schedule_logs',
}]
print('schedule tools:', schedule_tools)
assert len(schedule_tools) == 7, f'expected 7, got {len(schedule_tools)}'
print('OK: 7 tools registered')
"
```

Expected: `schedule tools: ['create_schedule', 'delete_schedule', 'get_schedule', 'list_my_schedules', 'list_schedule_logs', 'trigger_schedule', 'update_schedule']`。

- [ ] **Step 6: 提交**

```bash
git add backend/package/yuxi/agents/toolkits/schedules/tools.py backend/test/agent_scheduled/test_agent_schedule_tools.py
git commit -m "feat(schedule): 新增 trigger_schedule 工具并完成 7 工具集"
```

---

## Task 7: HTTP 路由修复（agent_config 归属校验 + 切换 owner-aware 方法）

**Files:**
- Modify: `backend/server/routers/schedule_router.py`
- Modify: `backend/test/test_schedule_router.py`（追加 case）

**修改点:**
1. 提取 `_verify_agent_ownership(db, agent_config_id, current_user)` helper（admin 跳过）
2. `create_schedule_route` 在构造 `ScheduleDefinition` 之前调一次
3. `update_schedule_route` 在 `update_data` 合并前调一次（当 `agent_config_id` 提供时）
4. 所有 schedule 读写调用（`get_by_id` / `update_schedule` / `delete_schedule` / `get_logs_by_schedule_id`）切换为 owner-aware 方法；admin 路径显式传 `is_admin=True`
5. **不修改** HTTP API 契约（Design Doc Non-Goals：URL 路径、请求/响应模型不变）

**参考决策:** Decision 4、5、7。

- [ ] **Step 1: 提取 helper**

在 `schedule_router.py` 顶部（`_is_admin` 之后，约 line 44）追加：

```python
async def _verify_agent_ownership(db: AsyncSession, agent_config_id: int, current_user: User) -> None:
    """校验 agent_config 归属当前用户；失败抛 403。admin 跳过（占位 body，Step 7 重写）。"""
    if _is_admin(current_user):
        return
    config_repo = AgentConfigRepository(db)
    config_item = config_repo  # placeholder; will be replaced in Step 7
```

> 先建 async 占位，让 Step 4/5 中的 `await _verify_agent_ownership(...)` 编译通过；最终 body 在 Step 7 重写。

- [ ] **Step 2: 写失败测试 — create 路由 agent 归属校验**

在 `backend/test/test_schedule_router.py` 末尾追加：

```python
async def test_create_schedule_rejects_foreign_agent(
    test_client, admin_headers, standard_user
):
    """普通用户绑定非自己的 agent_config_id 创建 schedule 应被 403 拒绝。"""
    user_headers = standard_user["headers"]

    # 用 admin 创建一个 agent_config
    create_cfg = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"admin_owned_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=admin_headers,
    )
    assert create_cfg.status_code == 200
    admin_owned_config_id = create_cfg.json()["config"]["id"]

    # 普通用户尝试绑定 admin 拥有的 config
    res = await test_client.post(
        "/api/schedules",
        json={
            "name": "越权测试",
            "agent_config_id": admin_owned_config_id,
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "query": "hi",
        },
        headers=user_headers,
    )
    assert res.status_code == 403, res.text
    assert "无权" in res.json()["detail"]


async def test_update_schedule_rejects_foreign_agent(
    test_client, admin_headers, standard_user
):
    """普通用户 update 时把 agent_config_id 切换为他人拥有的应被 403 拒绝。"""
    user_headers = standard_user["headers"]

    # 用户自己的 config
    own_cfg_res = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"user_owned_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=user_headers,
    )
    assert own_cfg_res.status_code == 200, own_cfg_res.text
    own_config_id = own_cfg_res.json()["config"]["id"]

    # 用这个 config 建一个 schedule
    create_res = await test_client.post(
        "/api/schedules",
        json={
            "name": "合法任务",
            "agent_config_id": own_config_id,
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "query": "hi",
        },
        headers=user_headers,
    )
    assert create_res.status_code == 200, create_res.text
    schedule_id = create_res.json()["data"]["id"]

    # admin 的 config
    admin_cfg_res = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"admin_cfg_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=admin_headers,
    )
    admin_cfg_id = admin_cfg_res.json()["config"]["id"]

    # 越权 update
    upd_res = await test_client.put(
        f"/api/schedules/{schedule_id}",
        json={"agent_config_id": admin_cfg_id},
        headers=user_headers,
    )
    assert upd_res.status_code == 403, upd_res.text


async def test_admin_can_bind_any_agent_when_creating_schedule(
    test_client, admin_headers
):
    """admin 创建 schedule 时绑定任何 agent_config 都应成功。"""
    cfg_res = await test_client.post(
        "/api/chat/agent/ChatbotAgent/configs",
        json={"name": f"any_cfg_{uuid.uuid4().hex[:6]}", "config_json": {}},
        headers=admin_headers,
    )
    cfg_id = cfg_res.json()["config"]["id"]

    res = await test_client.post(
        "/api/schedules",
        json={
            "name": "admin 任务",
            "agent_config_id": cfg_id,
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "query": "hi",
        },
        headers=admin_headers,
    )
    assert res.status_code == 200, res.text
```

- [ ] **Step 3: 跑测试确认失败**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_router.py -v
```

Expected: 3 个新 case FAIL（路由还没有 403 校验）。

- [ ] **Step 4: 在 `create_schedule_route` 加 agent 归属校验**

修改 `create_schedule_route`（`schedule_router.py:54-92`），在 `next_run` 计算前插入：

```python
@schedule_router.post("")
async def create_schedule_route(
    payload: ScheduleCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建定时任务"""
    try:
        # 校验 agent_config 归属（admin 跳过）
        if payload.agent_config_id is not None:
            await _verify_agent_ownership(db, payload.agent_config_id, current_user)

        next_run = None
        if payload.enabled:
            try:
                next_run = compute_next_run(payload.cron_expr, payload.timezone)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Cron 表达式或时区错误: {e}")
        # ... 后续不变
```

- [ ] **Step 5: 在 `update_schedule_route` 加 agent 归属校验**

修改 `update_schedule_route`（`schedule_router.py:132-172`），在 `update_data = payload.model_dump(exclude_unset=True)` 之前插入：

```python
        # 若替换 agent_config_id，先校验归属
        if payload.agent_config_id is not None:
            await _verify_agent_ownership(db, payload.agent_config_id, current_user)
```

- [ ] **Step 6: （跳过）占位 helper 由 Step 7 一次性替换**

本步骤在原计划中提供一个临时同步占位，与 Step 7 矛盾。**直接删除** Step 1 占位，进入 Step 7 一次性写出最终 async 实现即可。中间不需要任何过渡占位 —— Step 4/5 中的 `await _verify_agent_ownership(...)` 在 Step 7 完成后才能跑通。

- [ ] **Step 7: 完整重写 `_verify_agent_ownership`**

修改 `schedule_router.py` 顶部 import 区域，加入 `AgentConfigRepository`：

```python
from yuxi.repositories.agent_config_repository import AgentConfigRepository
```

替换 `_verify_agent_ownership`：

```python
async def _verify_agent_ownership(db: AsyncSession, agent_config_id: int, current_user: User) -> None:
    """校验 agent_config 归属当前用户；失败抛 403。admin 跳过。"""
    if _is_admin(current_user):
        return
    config_item = await AgentConfigRepository(db).get_by_id(agent_config_id)
    if config_item is None or str(config_item.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="无权使用该 agent")
```

> Step 4 / Step 5 中 `_verify_agent_ownership(...)` 的调用点已在 Step 4/5 中显式 `await`；本步骤只负责重写函数体本身。
> 字段类型说明（对照 Design Doc L74 与 tasks.md 2.1）：`config_item.user_id` 与 `current_user.user_id` 都是 String 登录 ID（如 `"u_123"`）；`current_user.id` 是 Integer 主键。比较时必须把整数侧的 `current_user.id` 序列化为字符串，避免类型不一致导致永远返回 403。

- [ ] **Step 8: 把所有 schedule 读写切换为 owner-aware**

逐个替换 `schedule_router.py` 中的仓储调用：

| 路由 | 原调用 | 替换为 |
|---|---|---|
| `get_schedule_route` (line 122) | `repo.get_by_id(schedule_id)` | `await repo.get_by_id_for_user(schedule_id, str(current_user.id), is_admin=_is_admin(current_user))` |
| `update_schedule_route` (line 142) | `repo.get_by_id(schedule_id)` | 同上（保留 404 行为） |
| `update_schedule_route` (line 165) | `repo.update_schedule(...)` | `await repo.update_for_user(schedule_id, str(current_user.id), update_data, is_admin=_is_admin(current_user))` |
| `delete_schedule_route` (line 184) | `repo.get_by_id(schedule_id)` | `await repo.get_by_id_for_user(...)` |
| `delete_schedule_route` (line 191) | `repo.delete_schedule(...)` | `await repo.delete_for_user(schedule_id, str(current_user.id), is_admin=_is_admin(current_user))` |
| `patch_schedule_route` (line 211) | `repo.get_by_id(schedule_id)` | `await repo.get_by_id_for_user(...)` |
| `patch_schedule_route` (line 230) | `repo.update_schedule(...)` | `await repo.update_for_user(schedule_id, str(current_user.id), update_data, is_admin=_is_admin(current_user))` |
| `trigger_schedule_route` (line 249) | `repo.get_by_id(schedule_id)` | `await repo.get_by_id_for_user(...)` |
| `list_schedule_logs_route` (line 278) | `repo.get_by_id(schedule_id)` | `await repo.get_by_id_for_user(...)` |
| `list_schedule_logs_route` (line 285) | `repo.get_logs_by_schedule_id(...)` | `await repo.list_logs_for_user(schedule_id, str(current_user.id), limit=..., offset=..., is_admin=...)` |

> 由于已经把 owner 校验下沉到仓储方法，原来路由中的 `if not _is_admin(current_user) and schedule.user_id != ...` 显式分支可以删除；找不到/越权统一由 `*_for_user` 返回 `None` 然后路由层 `_raise_not_found()`。

- [ ] **Step 9: 跑集成测试**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_router.py -v
```

Expected: 全部 PASS（含原 1 个 + 新增 3 个）。

- [ ] **Step 10: 跑仓储 + 工具单测回归**

```bash
docker compose exec api uv run --group test pytest backend/test/test_schedule_repository.py backend/test/agent_scheduled/test_agent_schedule_tools.py -v
```

Expected: 全部 PASS（12 + 20）。

- [ ] **Step 11: 提交**

```bash
git add backend/server/routers/schedule_router.py backend/test/test_schedule_router.py
git commit -m "fix(schedule): 修复 agent_config 跨用户绑定越权 + 切换 owner-aware 仓储方法"
```

---

## Task 8: 文档更新 + 最终验证

**Files:**
- Modify: `docs/develop-guides/roadmap.md`
- Create: `docs/agents/agent-schedule-tools.md`
- Modify: `docs/.vitepress/config.mts`（在 `agents` 导航添加入口）

- [ ] **Step 1: 更新 `roadmap.md`**

在 `docs/develop-guides/roadmap.md` 顶部"近期变更"区域追加：

```markdown
- 2026-08-03 `agent-schedule-toolkit`（OpenSpec change）：在 agent 运行时新增 7 个 schedule 管理 `@tool`；把"按用户隔离"下沉到 `ScheduleRepository` 的 4 个 owner-aware 方法；修复 `create_schedule_route` / `update_schedule_route` 中 `agent_config_id` 归属校验缺失的越权漏洞。详见 `docs/agents/agent-schedule-tools.md`。
```

- [ ] **Step 2: 新建 `docs/agents/agent-schedule-tools.md`**

```markdown
# agent-schedule-tools

Yuxi-Know 的 agent 在对话中可以直接调用以下 7 个 schedule 管理工具，无需离开对话流。

## 可用工具

| 工具名 | 说明 |
|---|---|
| `list_my_schedules` | 列出当前用户的定时任务；admin 看到全部 |
| `get_schedule` | 查看单条定时任务详情 |
| `create_schedule` | 创建新的定时任务；agent_config_id 必须归属当前用户 |
| `update_schedule` | 更新定时任务字段；agent_config_id 归属校验同 create |
| `delete_schedule` | 删除定时任务 |
| `trigger_schedule` | 立即触发一次（不影响原 cron 周期） |
| `list_schedule_logs` | 查看历史执行日志 |

## 用户隔离

所有工具按 `runtime.context.user_id` 强制 owner 隔离。

- 普通用户只能看到 / 操作自己创建的 schedule
- 绑定 `agent_config_id` 时必须归属当前用户（admin 跳过）
- 失败统一返回中文错误消息，不区分"未找到"与"无权访问"，避免泄露存在性

## 在哪里开启

`SubAgent` / `Agent` 配置页 → 工具列表 → 勾选上述 7 个工具。

`BaseContext.tools` 默认列表中**未**包含这些工具（避免自动启用带来预期外行为）。
```

- [ ] **Step 3: 注册 VitePress 导航**

打开 `docs/.vitepress/config.mts`，找到 `agents` 导航分组，加入：

```ts
{
  text: "agent-schedule-tools",
  link: "/agents/agent-schedule-tools",
}
```

- [ ] **Step 4: 跑全套测试 + 格式化**

```bash
docker compose exec api make format
docker compose exec api uv run --group test pytest backend/test -v
```

Expected: 全部 PASS；`make format` 无 diff。

- [ ] **Step 5: 端到端冒烟（Design Doc Testing Strategy 节）**

按 Design Doc Testing Strategy 节执行：

```bash
docker compose up -d api-dev web-dev
# 登录 web → AgentView 选一个有 7 工具的 agent → 勾选全部
# 对话中：list_my_schedules → 仅看本人 schedule
# 对话中：create_schedule(agent_config_id=<他人 agent id>) → 收到"无权使用该 agent"
# 对话中：trigger_schedule → 调 list_schedule_logs 看到 1 条新日志
```

- [ ] **Step 6: 提交**

```bash
git add docs/develop-guides/roadmap.md docs/agents/agent-schedule-tools.md docs/.vitepress/config.mts
git commit -m "docs(schedule): 记录 agent-schedule-toolkit 能力与用户隔离契约"
```

- [ ] **Step 7: 创建 PR**

```bash
git push -u origin HEAD
gh pr create --base main --title "feat: 在 agent 运行时新增 schedule 管理工具集并加固按用户隔离" --body-file .github/PULL_REQUEST_TEMPLATE.md
```

PR 描述勾选 PULL_REQUEST_TEMPLATE 中所有相关项；正文注明本 change 关联 `docs/openspec/changes/agent-schedule-toolkit/`。

---

## Self-Review

### Spec 覆盖度

| Design Doc 决策 / 章节 | 对应任务 |
|---|---|
| Decision 1（工具实现位置） | Task 2 |
| Decision 2（用户上下文来源） | Task 2 (_resolve_user)、Task 3-6 |
| Decision 3（数据访问） | Task 2 (helpers)、Task 3-6 |
| Decision 4（owner-aware 仓储） | Task 1 |
| Decision 5（agent_config 归属校验） | Task 4 (工具)、Task 7 (路由) |
| Decision 6（工具返回格式与分页） | Task 3、5 |
| Decision 7（admin 路径） | Task 1-7 各处显式 `is_admin=` |
| Decision 8（agent_config_id 整数 PK） | Task 4 `CreateScheduleInput` / `UpdateScheduleInput` |
| Decision 9（时区与 cron 校验） | Task 4 `_check_agent_ownership` 之前的 `compute_next_run` |
| Decision 10（trigger 工具实现） | Task 6 |
| Components A-H | Task 2-7 一一对应 |
| Error Handling 表 | Task 3-6 各工具实现 |
| Testing Strategy（工具测试） | Task 3-6 测试 |
| Testing Strategy（路由测试） | Task 7 测试 |
| Testing Strategy（仓储测试） | Task 1 测试 |
| Migration Plan | 无 schema 变更；Task 8 端到端冒烟验证热重载 |
| Future Work | 不在本 change 范围 |

### 占位符扫描

通读全文未发现 "TBD" / "TODO" / "implement later" / "similar to Task N" 类型的占位符；所有代码块均给出可执行内容；类型 / 方法名（如 `get_by_id_for_user` / `_is_admin` / `_check_agent_ownership`）在引入任务和消费任务间保持一致。

### 类型一致性

- `get_by_id_for_user(schedule_id, user_id, *, is_admin)` ↔ Task 1 引入，Task 3-7 全部按此签名调用 ✓
- `_check_agent_ownership(session, agent_config_id, user_id, is_admin) -> str | None` ↔ Task 2 定义，Task 4 调用 ✓
- `_is_admin(runtime, db_session) -> bool` ↔ Task 2 定义，Task 3-7 调用 ✓
- `LIST_DEFAULT_LIMIT = 20` / `LIST_MAX_LIMIT = 100` ↔ Task 3 定义，Task 5 复用 ✓
- 所有 Pydantic schema 都不含 `user_id` 字段 ✓
- 路由层校验时把 `current_user.id`（Integer PK）序列化为字符串后与 String 的 `config_item.user_id` 比较；与 Design Doc L74 和 tasks.md 2.1 一致 ✓
