"""轻量健康探针的失败边界和导入隔离。"""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize(
    "value,ttl,expected",
    [
        (b"alive", 1000, 0),
        (b"alive", 6000, 0),
        (None, -2, 1),
        (b"", 1000, 1),
        (b"alive", -1, 1),
        (b"alive", 0, 1),
        (b"alive", 6001, 1),
    ],
)
def test_health_requires_live_bounded_lease(monkeypatch, value, ttl, expected):
    """缺失、空值、永久或超长租约不能被视为健康。"""
    from yuxi.services import worker_health

    client = MagicMock()
    client.pipeline.return_value.__enter__.return_value.execute.return_value = (value, ttl)
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(worker_health, "sync_redis_client", lambda *args, **kwargs: context)
    monkeypatch.setattr(worker_health, "WORKER_HEALTH_MAX_TTL_MS", 6000)
    assert worker_health.main() == expected


def test_health_connection_error_does_not_expose_credentials(monkeypatch, capsys):
    """连接失败返回非零且不输出异常中的凭据。"""
    from yuxi.services import worker_health

    context = MagicMock()
    context.__enter__.side_effect = ConnectionError("redis://user:secret@host/0")
    monkeypatch.setattr(worker_health, "sync_redis_client", lambda *args, **kwargs: context)
    assert worker_health.main() == 1
    assert "secret" not in capsys.readouterr().err


def test_health_import_does_not_load_business_runtime():
    """干净解释器在禁止业务运行时导入时仍可加载探针。"""
    script = """
import importlib.abc
import sys
class BlockBusiness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('yuxi.services.run_worker', 'yuxi.services.run_queue_service',
                   'langgraph', 'sqlalchemy', 'tiktoken')
        if fullname.startswith(blocked):
            raise AssertionError('health probe imported business runtime: ' + fullname)
sys.meta_path.insert(0, BlockBusiness())
import yuxi.services.worker_health
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
