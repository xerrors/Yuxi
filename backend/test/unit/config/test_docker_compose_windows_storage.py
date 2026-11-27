"""Windows 覆盖只替换用户数据，并让所有读写者使用同一 Linux 卷。"""

from test.unit.config._shared import load_compose


def test_windows_storage_uses_one_project_scoped_volume_for_all_writers():
    """遗漏 runner 或 storage-init 会拆分文件事实，不能只修改 API 挂载。"""
    compose = load_compose("docker-compose.windows.yml")
    assert compose["volumes"] == {"user-data": None}
    assert set(compose["services"]) == {"api", "worker", "sandbox-runner", "storage-init"}
    for name, service in compose["services"].items():
        target = "/sandbox-data" if name == "sandbox-runner" else "/app/user-data"
        assert service["volumes"] == [{"type": "volume", "source": "user-data", "target": target, "volume": {"nocopy": True}}]
