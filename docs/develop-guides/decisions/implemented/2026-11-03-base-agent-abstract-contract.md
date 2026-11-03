# BaseAgent 构图契约在实例化期生效

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/base.py

## 问题

`BaseAgent` 以普通类声明，却用 `@abstractmethod` 标注 `get_graph`。缺少 `ABCMeta` 时该装饰器不参与实例化检查：漏实现 `get_graph` 的后端仍能创建，失败推迟到 `_stream_input_with_state` 中 `await self.get_graph(...)` 返回 `None` 后访问 `None.astream_events`，错误指向构图调用点而不是缺失的实现。

## 决策

### 实现方案

`BaseAgent` 继承 `ABC`，`get_graph` 保持抽象方法。后端工厂 `get_agent_backend` 与 `list_agent_backend_info` 继续按稳定 ID 创建轻量对象；未实现 `get_graph` 的后端类在实例化时抛出 `TypeError`，不会进入 `AGENT_BACKENDS` 的注册发现与运行链路。既有后端 `ChatbotAgent` 与测试后端均已实现 `get_graph`，行为不变。

## 替代方案

- 保留普通类并删除 `@abstractmethod`：契约从不可执行的装饰器变成没有契约，与模型、知识库、文档解析器等基类不一致，漏实现仍在运行期暴露。
- 在 `get_graph` 方法体内 `raise NotImplementedError`：失败仍然推迟到调用时，不满足实例化期发现的目的。
- 自定义 `__init_subclass__` 或元类做检查：重复标准库 `ABCMeta` 已有能力，增加维护面。

## 后果

- `BaseAgent` 自身不再可实例化，这是有意的契约表达。
- 漏实现 `get_graph` 的后端类在实例化期失败，`get_agent_backend` 抛出 `TypeError`，而不是构图后的 `AttributeError`。该错误不转换为 `AgentBackendNotFoundError`，因为它属于注册代码的缺陷而不是配置引用了不存在的后端。
- `ABC` 只保证 `get_graph` 存在，不保证返回已编译的图；返回 `None` 的子类仍会在构图调用处失败。
- 仓库外自定义后端如果漏实现 `get_graph`，升级后会在首次实例化或后端信息查询时提前失败。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 漏实现 `get_graph` 的子类无法实例化 | 普通类声明时仍可实例化 | base.py 与 `ABCMeta` | `pytest test/unit/agents/test_base_agent_contract.py` | 把 `BaseAgent` 还原为普通类，测试以 `DID NOT RAISE` 失败 | Passed |
| 已实现 `get_graph` 的后端仍可创建 | 继承 `ABC` 后阻断合法子类 | `agent_backends/__init__.py` | 全量 unit 中的 `test_builtin_discovery.py`、`test_base_agent_langfuse_config.py` | 让 `ChatbotAgent` 实例化失败即变红 | Passed |
| 其他基类的抽象方法都已继承 `ABC` | 同仓库仍存在无强制力的 `@abstractmethod` | 各基类所在模块 | 全仓符号搜索 | 不适用 | Inspected |

`docker run --rm ... ghcr.io/astral-sh/uv:python3.13-bookworm uv run --frozen --group test pytest test/unit -m "not slow" -q`：3530 passed、126 skipped、0 failed；skip 是仓库原有的环境门控用例，不计入通过。同一容器内 `python3 scripts/ci/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.ci.test_verify_engineering_contracts` 通过。本机 Windows 无法运行测试（测试链路使用 `os.O_DIRECTORY`、`fcntl` 等 POSIX 接口），上述结果为 Linux 容器内实际执行。本次不改变 HTTP、PostgreSQL、worker、SSE 或浏览器行为，未运行 integration 与 E2E。
