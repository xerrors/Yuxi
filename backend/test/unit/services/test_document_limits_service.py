"""配置默认值、部署硬上限与任务快照验证。"""

from unittest.mock import AsyncMock
import pytest
from yuxi.services import document_limits_service as svc


@pytest.fixture(autouse=True)
def clear_deployment(monkeypatch):
    """使用确定性默认值，不继承机器私有配置。"""
    for key in (
        "DOCUMENT_UPLOAD_MAX_MIB",
        "DOCUMENT_UPLOAD_HARD_MAX_MIB",
        "DOCUMENT_OCR_MAX_PAGES",
        "DOCUMENT_OCR_HARD_MAX_PAGES",
    ):
        monkeypatch.delenv(key, raising=False)


def test_defaults_and_deployment_caps(monkeypatch):
    """展示部署值与配置收紧后的实际值。"""
    assert svc.resolve_limits({})["upload_max_mib"] == 100
    monkeypatch.setenv("DOCUMENT_UPLOAD_HARD_MAX_MIB", "300")
    monkeypatch.setenv("DOCUMENT_UPLOAD_MAX_MIB", "200")
    assert svc.resolve_limits({})["defaults"]["upload_max_mib"] == 200
    state = svc.resolve_limits({"upload_max_mib": 400, "ocr_max_pages": 900, "revision": 7})
    assert state["effective_upload_max_bytes"] == 300 * svc.MIB
    assert state["ocr_max_pages"] == 500 and state["revision"] == 7


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2", 101])
@pytest.mark.asyncio
async def test_invalid_values_never_write(monkeypatch, value):
    """直接服务调用也守住整数和硬上限。"""
    write = AsyncMock()
    monkeypatch.setattr(svc, "replace_limits", write)
    with pytest.raises(ValueError):
        await svc.save_document_limits({"upload_max_mib": value}, 0, "admin")
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_is_separate_and_stable(monkeypatch):
    """已创建快照不随后续设置改变。"""
    read = AsyncMock(
        side_effect=[
            {"effective_upload_max_bytes": 100, "ocr_max_pages": 50, "revision": 2},
            {"effective_upload_max_bytes": 10, "ocr_max_pages": 5, "revision": 3},
        ]
    )
    monkeypatch.setattr(svc, "get_document_limits", read)
    first = await svc.snapshot_document_limits()
    second = await svc.snapshot_document_limits()
    assert first == {"max_file_bytes": 100, "max_ocr_pages": 50, "version": 2}
    assert second["version"] == 3


@pytest.mark.asyncio
async def test_write_commit_precedes_invalidation(monkeypatch):
    """写失败不得失效缓存或虚假返回成功。"""
    write = AsyncMock(side_effect=RuntimeError("database down"))
    invalidate = AsyncMock()
    monkeypatch.setattr(svc, "replace_limits", write)
    monkeypatch.setattr(svc, "invalidate_option_cache", invalidate)
    with pytest.raises(RuntimeError):
        await svc.save_document_limits({"upload_max_mib": 50, "ocr_max_pages": 20}, 0, "admin")
    invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_acceptance_reads_pg_even_when_redis_invalidation_fails(monkeypatch):
    """Redis失效失败时，新接收任务仍以已提交PG值为准。"""
    values = {"upload_max_mib": 100, "ocr_max_pages": 500, "revision": 0}

    async def write(value, revision, actor):
        values.clear()
        values.update(value, revision=revision + 1)

    async def read():
        return dict(values)

    from types import SimpleNamespace
    from yuxi.config import options

    # 执行真实失效包装，实际Redis调用边界注入故障。
    monkeypatch.setattr(
        options,
        "get_async_redis_client",
        AsyncMock(return_value=SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError("redis unavailable")))),
    )
    monkeypatch.setattr(svc, "replace_limits", write)
    monkeypatch.setattr(svc, "read_limits", read)
    result = await svc.save_document_limits({"upload_max_mib": 1, "ocr_max_pages": 2}, 0, "admin")
    assert result["revision"] == 1 and result["upload_max_mib"] == 1
    assert await svc.snapshot_document_limits() == {"max_file_bytes": svc.MIB, "max_ocr_pages": 2, "version": 1}
