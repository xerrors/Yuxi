"""E2E Run 轮询自身的等待预算。"""

import asyncio

import pytest

from test.e2e import e2e_helpers


@pytest.mark.asyncio
async def test_wait_for_run_cancels_blocked_status_request_at_deadline(monkeypatch):
    """状态接口卡住时，单次 HTTP 请求不能越过 Run 总期限。"""
    monkeypatch.setattr(e2e_helpers, "RUN_TIMEOUT_SECONDS", 0.02)

    class BlockedClient:
        async def get(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    with pytest.raises(pytest.fail.Exception, match="Run timed out"):
        await e2e_helpers.wait_for_run(BlockedClient(), {}, "blocked-run")
