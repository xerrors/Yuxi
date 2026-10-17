"""命令取消必须到达 GEM session，不能只结束 Python 等待。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during_start", [False, True])
async def test_cancel_terminates_the_started_session_before_return(monkeypatch, cancel_during_start):
    """启动确认前后的取消均收敛同一个命令，取消后不得产生延迟副作用。"""
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="cancel-thread", uid="cancel-user")
    monkeypatch.setattr(backend, "_get_connection", lambda: SimpleNamespace(sandbox_url="http://sandbox"))
    entered, started, allow_start, stopped, killing, allow_kill = (asyncio.Event() for _ in range(6))
    active = set()

    async def launch(**kwargs):
        """模拟启动响应在请求到达远端后延迟返回。"""
        entered.set()
        if cancel_during_start:
            await allow_start.wait()
        active.add(kwargs["id"])
        started.set()
        return SimpleNamespace(data=SimpleNamespace(status="running"))

    async def kill(*, id, **_kwargs):
        """只有显式终止能清除模拟远端进程。"""
        assert started.is_set()
        killing.set()
        await allow_kill.wait()
        active.remove(id)
        stopped.set()

    shell = SimpleNamespace(
        create_session=AsyncMock(),
        exec_command=launch,
        kill_process=kill,
    )

    async def wait(**_kwargs):
        """模拟长命令尚未结束。"""
        await asyncio.Event().wait()

    shell.wait_for_process = wait
    monkeypatch.setattr(backend, "_build_async_client", lambda *_args: SimpleNamespace(shell=shell))
    monkeypatch.setattr(backend, "execute", lambda *_args, **_kwargs: pytest.fail("异步命令仍进入同步线程"))
    task = asyncio.create_task(backend.aexecute("long command", timeout=3600))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        allow_start.set()
        await asyncio.wait_for(killing.wait(), 1)
        task.cancel()
        allow_kill.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert stopped.is_set()
        assert active == set()
    finally:
        allow_start.set()
        allow_kill.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_async_execute_returns_completed_output_and_hard_timeout(monkeypatch):
    """短轮询只在命令终止后交付最终输出。"""
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="command-thread", uid="command-user")
    monkeypatch.setattr(backend, "_get_connection", lambda: SimpleNamespace(sandbox_url="http://sandbox"))
    shell = SimpleNamespace(
        create_session=AsyncMock(),
        exec_command=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(status="running"))),
        wait_for_process=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(status="completed"))),
        view=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(output="done", exit_code=0))),
        kill_process=AsyncMock(),
    )
    monkeypatch.setattr(backend, "_build_async_client", lambda *_args: SimpleNamespace(shell=shell))
    result = await backend.aexecute("echo done", timeout=3600)
    assert result.output == "done"
    assert result.exit_code == 0
    assert shell.exec_command.call_args.kwargs["hard_timeout"] == 3600
    assert shell.exec_command.call_args.kwargs["async_mode"] is True
    assert shell.wait_for_process.call_args.kwargs["max_wait_seconds"] <= 2
    shell.kill_process.assert_not_awaited()


def test_hard_timeout_without_exit_code_is_a_failed_command(monkeypatch):
    """GEM 超时无退出码时不得呈现成未知或成功。"""
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="timeout-thread", uid="timeout-user")
    response = backend._execute_response(SimpleNamespace(status="hard_timeout", output="partial", exit_code=None))
    assert response.exit_code == 124
    assert response.output == "partial"
