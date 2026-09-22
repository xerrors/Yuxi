# 子智能体忙异常支持 traceback 赋值

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/subagent_run_service.py

## 问题

已有活跃子 Run 时，SubagentRunBusy 穿过异步上下文管理器；标准库写入 __traceback__ 被 frozen dataclass 拒绝，busy 工具结果丢失。

## 决策

SubagentRunBusy 使用普通 dataclass，允许异常协议修改 traceback。字段和 to_payload 保持现有契约。修复没有需要先裁决的替代语义，因此直接记录实现。

## 替代方案

在调用方绕开上下文管理器或转换异常会增加分支，且不能修复其他异常传播路径。保持 frozen 会继续违反异常协议。

## 后果

异常字段可修改；SubagentStartResult 仍冻结。派发、事务、权限和队列逻辑保持现状。

## 验证

在 backend 运行 `uv run --frozen --group test pytest test/unit/middlewares/test_subagent_task_middleware.py test/unit/services/test_subagent_run_service.py -q`，36 passed。回归用真实 asynccontextmanager 包裹模拟 service，断言 busy 工具消息的完整 payload。上游实现以 FrozenInstanceError 失败。本地完整 unit 为 2273 passed。Compose API/worker 环境未配置，真实 worker E2E 未运行；单测不证明整轮 Run 终态。
