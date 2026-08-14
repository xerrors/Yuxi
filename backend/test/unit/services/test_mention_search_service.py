from __future__ import annotations

import base64
from pathlib import Path
import pytest

import ormsgpack
import yuxi.services.mention_search_service as mention_service


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.expire_calls: dict[str, int] = {}
        self.delete_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.expire_calls[key] = ex

    async def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.data.pop(key, None)


@pytest.fixture
def mock_sandbox_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 创建模拟的工作区、上传、输出目录
    workspace_dir = tmp_path / "shared" / "user_1" / "workspace"
    uploads_dir = tmp_path / "threads" / "thread_1" / "user-data" / "uploads"
    outputs_dir = tmp_path / "threads" / "thread_1" / "user-data" / "outputs"

    workspace_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Mock sandbox_paths 的函数
    monkeypatch.setattr(mention_service, "sandbox_workspace_dir", lambda t, u: workspace_dir)
    monkeypatch.setattr(mention_service, "sandbox_uploads_dir", lambda t: uploads_dir)
    monkeypatch.setattr(mention_service, "sandbox_outputs_dir", lambda t: outputs_dir)

    return {
        "workspace": workspace_dir,
        "uploads": uploads_dir,
        "outputs": outputs_dir,
    }


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()

    async def mock_get_redis():
        return redis

    monkeypatch.setattr(mention_service, "get_redis_client", mock_get_redis)
    return redis


@pytest.mark.asyncio
async def test_scan_pruned_files_and_exclude_dirs(mock_sandbox_paths):
    workspace = mock_sandbox_paths["workspace"]

    # 创建常规文件
    (workspace / "main.py").write_text("print('hello')")
    (workspace / "utils.py").write_text("def run(): pass")

    # 创建被排除的目录和文件
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]")

    node_modules = workspace / "node_modules"
    node_modules.mkdir()
    (node_modules / "express.js").write_text("module.exports = {}")

    # 扫描
    results = mention_service._scan_pruned_files(workspace, 100)

    # 校验
    files = {name for name, _ in results}
    assert "main.py" in files
    assert "utils.py" in files
    assert "config" not in files
    assert "express.js" not in files


@pytest.mark.asyncio
async def test_scan_depth_protection(mock_sandbox_paths):
    workspace = mock_sandbox_paths["workspace"]

    # 创建超深的文件树路径：超过 15 层
    deep_dir = workspace
    for i in range(18):
        deep_dir = deep_dir / f"dir_{i}"

    deep_dir.mkdir(parents=True, exist_ok=True)
    (deep_dir / "deep_file.py").write_text("deep")

    # 扫描
    results = mention_service._scan_pruned_files(workspace, 100)
    files = {name for name, _ in results}

    # 深度限制应成功剪枝拦截该超深文件
    assert "deep_file.py" not in files


@pytest.mark.asyncio
async def test_scan_width_limit(mock_sandbox_paths):
    workspace = mock_sandbox_paths["workspace"]

    # 创建 600 个扁平的小文件
    for i in range(600):
        (workspace / f"file_{i}.py").write_text(str(i))

    # 扫描，设置 max_entries = 1000（看看单目录 500 的宽度限额是否起作用）
    results = mention_service._scan_pruned_files(workspace, 1000)

    # 限制单目录 MAX_ENTRIES_PER_DIR = 500 熔断
    assert len(results) == 500


@pytest.mark.asyncio
async def test_mention_cache_lifecycle_with_ormsgpack(mock_sandbox_paths, fake_redis):
    workspace = mock_sandbox_paths["workspace"]
    uploads = mock_sandbox_paths["uploads"]

    (workspace / "main.py").write_text("main")
    (uploads / "data.csv").write_text("csv")

    # 1. 首次查询：构建缓存并存入 Redis
    index_1 = await mention_service.get_or_build_file_index("thread_1", "user_1")
    assert len(index_1) == 2

    # 验证 Redis 中已按 workspace/thread 分别缓存
    workspace_redis_key = f"{mention_service.WORKSPACE_CACHE_PREFIX}user_1"
    thread_redis_key = f"{mention_service.THREAD_CACHE_PREFIX}thread_1"
    cached_workspace = fake_redis.data.get(workspace_redis_key)
    cached_thread = fake_redis.data.get(thread_redis_key)
    assert cached_workspace is not None
    assert cached_thread is not None

    # 反序列化校验
    workspace_entries = ormsgpack.unpackb(base64.b64decode(cached_workspace))
    thread_entries = ormsgpack.unpackb(base64.b64decode(cached_thread))
    assert len(workspace_entries) == 1
    assert len(thread_entries) == 1

    # 2. 修改磁盘文件，但在 TTL 内应仍然走 Redis 缓存，内容不更新
    (workspace / "new_file.py").write_text("new")
    index_2 = await mention_service.get_or_build_file_index("thread_1", "user_1")
    assert len(index_2) == 2  # 仍然命中缓存，没有扫描出 new_file.py

    # 3. 清理 workspace 缓存后重新读取，应成功更新磁盘扫描内容
    await mention_service.invalidate_workspace_mention_cache("user_1")
    assert fake_redis.data.get(workspace_redis_key) is None

    index_3 = await mention_service.get_or_build_file_index("thread_1", "user_1")
    assert len(index_3) == 3
    assert any(name == "new_file.py" for name, _, source in index_3 if source == "workspace")


@pytest.mark.asyncio
async def test_search_thread_source_before_workspace(mock_sandbox_paths, fake_redis):
    workspace = mock_sandbox_paths["workspace"]
    uploads = mock_sandbox_paths["uploads"]
    (workspace / "report.md").write_text("workspace")
    (uploads / "report.md").write_text("thread")

    results = await mention_service.search_mention_files_in_index("thread_1", "user_1", "report")

    assert len(results) == 2
    assert results[0]["path"] == "/home/gem/user-data/uploads/report.md"
    assert results[0]["source"] == "thread"
    assert results[1]["path"] == "/home/gem/user-data/workspace/report.md"
    assert results[1]["source"] == "workspace"


@pytest.mark.asyncio
async def test_search_mention_files_in_index(mock_sandbox_paths, fake_redis):
    workspace = mock_sandbox_paths["workspace"]
    uploads = mock_sandbox_paths["uploads"]
    (workspace / "agent_config.json").write_text("config")
    (workspace / "main.py").write_text("main")
    (workspace / "guide.md").write_text("workspace")
    (uploads / "guide.csv").write_text("thread")

    # 搜索匹配测试
    results = await mention_service.search_mention_files_in_index("thread_1", "user_1", "config")
    assert len(results) == 1
    assert results[0]["name"] == "agent_config.json"
    assert results[0]["path"] == "/home/gem/user-data/workspace/agent_config.json"
    assert results[0]["is_dir"] is False
    assert results[0]["source"] == "workspace"

    # 大小写不敏感匹配
    results_case = await mention_service.search_mention_files_in_index("thread_1", "user_1", "MAIN")
    assert len(results_case) == 1
    assert results_case[0]["name"] == "main.py"

    # 不带 thread_id 时仅搜 workspace 单来源，线程附件不参与
    results_no_thread = await mention_service.search_mention_files_in_index(None, "user_1", "guide")
    assert len(results_no_thread) == 1
    assert results_no_thread[0]["name"] == "guide.md"
    assert results_no_thread[0]["path"] == "/home/gem/user-data/workspace/guide.md"
    assert results_no_thread[0]["source"] == "workspace"


@pytest.mark.asyncio
async def test_search_mention_directories_and_weighted_ranking(mock_sandbox_paths, fake_redis):
    workspace = mock_sandbox_paths["workspace"]

    # 1. 创建合格的子目录 "test"
    test_dir = workspace / "test"
    test_dir.mkdir(exist_ok=True)

    # 2. 在子目录下创建一些包含关键字的文件
    (test_dir / "test_auth.py").write_text("auth")
    (test_dir / "conftest.py").write_text("conf")  # 文件名不含 test，但路径含 test

    # 3. 搜索 "@test"
    results = await mention_service.search_mention_files_in_index("thread_1", "user_1", "test")

    # 4. 校验结果
    # 必须包含 3 个项：目录 "test/"，文件 "test_auth.py"，文件 "conftest.py" (路径匹配兜底)
    assert len(results) == 3

    # 5. 校验置顶排序和 is_dir 属性
    # 由于目录名字 "test" 与搜索词 "test" 100% 完全一致，得分为最高 (1000分)，必须排在第 1 位
    assert results[0]["name"] == "test"
    assert results[0]["is_dir"] is True
    assert results[0]["path"] == "/home/gem/user-data/workspace/test/"

    # "test_auth.py" 文件名以 "test" 开头，为前缀匹配 (500分)，必须排在第 2 位
    assert results[1]["name"] == "test_auth.py"
    assert results[1]["is_dir"] is False

    # "conftest.py" 文件名不含 test，为纯路径匹配兜底 (10分)，必须排在最后
    assert results[2]["name"] == "conftest.py"
    assert results[2]["is_dir"] is False
