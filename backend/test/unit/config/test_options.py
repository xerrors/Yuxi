from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.support.sqlite import create_utc_sqlite_engine
from yuxi.bootstrap.options import ensure_options_in_db, get_option_definitions
from yuxi.infrastructure.postgres.base import Base
from yuxi.modules.documents.options import mineru_official_api_opts
from yuxi.modules.knowledge.options import knowledge_url_import_policy
from yuxi.modules.models.providers.builtin import BUILTIN_PROVIDERS
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.options import system_options
from yuxi.modules.system.services import options


def test_knowledge_url_import_option_is_registered_with_closed_default(monkeypatch):
    """新部署默认关闭 URL 导入，定义通过现有初始化入口汇总。"""
    monkeypatch.delenv("YUXI_URL_WHITELIST", raising=False)
    assert get_option_definitions()[knowledge_url_import_policy.key] is knowledge_url_import_policy
    assert knowledge_url_import_policy.resolve({}) == {"allowed_hosts": []}


def test_list_option_environment_is_split_and_explicit_empty_wins(monkeypatch):
    """部署域名按列表读取，管理员清空后不能退回环境白名单。"""
    definition = options.Option(
        key="fixture_policy",
        name="测试来源",
        description="测试列表环境变量。",
        params={"fields": [{"key": "allowed_hosts", "type": "list[str]", "environment": "FIXTURE_ALLOWED_HOSTS", "default": []}]},
    )
    monkeypatch.setenv("FIXTURE_ALLOWED_HOSTS", " example.com, , docs.example.org ")
    assert definition.resolve({}) == {"allowed_hosts": ["example.com", "docs.example.org"]}
    assert definition.resolve({"allowed_hosts": []}) == {"allowed_hosts": []}
    assert definition.resolve({"allowed_hosts": ["saved.test"]}) == {"allowed_hosts": ["saved.test"]}
    monkeypatch.delenv("FIXTURE_ALLOWED_HOSTS")
    assert definition.resolve({}) == {"allowed_hosts": []}


def test_option_effective_value_displays_environment_without_secrets(monkeypatch):
    """表单保留原始值，同时只公开非敏感字段的当前有效值。"""
    monkeypatch.setenv("FIXTURE_ALLOWED_HOSTS", "example.com,docs.example.org")
    monkeypatch.setenv("FIXTURE_SECRET", "private-environment-sentinel")
    record = ConfigOption(
        key="fixture_policy",
        name="测试来源",
        description="测试有效值。",
        params={
            "fields": [
                {"key": "allowed_hosts", "type": "list[str]", "environment": "FIXTURE_ALLOWED_HOSTS", "default": []},
                {"key": "api_key", "type": "password", "sensitive": True, "environment": "FIXTURE_SECRET"},
            ]
        },
        value={"api_key": "private-database-sentinel"},
    )
    serialized = options.serialize_option(record)
    assert serialized["effective_value"] == {"allowed_hosts": ["example.com", "docs.example.org"]}
    assert serialized["value"] == {"api_key": ""}
    assert "private-environment-sentinel" not in json.dumps(serialized)
    assert "private-database-sentinel" not in json.dumps(serialized)


@pytest.mark.parametrize("key", ["default_model", "fast_model"])
def test_system_chat_model_defaults_to_deepseek_flash(key):
    """未配置的对话与快速模型使用硅基流动 DeepSeek Flash。"""
    assert system_options.resolve({})[key] == "siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash"


def test_system_chat_model_defaults_are_enabled_builtin_models():
    """默认模型必须存在于内置硅基流动模型列表，否则新部署会引用不可用模型。"""
    siliconflow = next(provider for provider in BUILTIN_PROVIDERS if provider["provider_id"] == "siliconflow-cn")
    enabled_specs = {f"siliconflow-cn:{model['id']}" for model in siliconflow["enabled_models"]}
    resolved = system_options.resolve({})

    assert {resolved["default_model"], resolved["fast_model"]} <= enabled_specs


@pytest.mark.parametrize("key", ["default_model", "fast_model"])
def test_system_chat_model_preserves_saved_selection(key):
    """默认值更新不覆盖管理员已保存的模型选择。"""
    saved_model = "siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5"
    assert system_options.resolve({key: saved_model})[key] == saved_model


def test_reference_model_defaults_to_empty_and_keeps_admin_selection():
    """空标注配置由用例沿用快速模型，管理员显式选择保持独立。"""
    assert system_options.resolve({})["reference_model"] == ""
    assert system_options.resolve({"reference_model": "provider:references"})["reference_model"] == "provider:references"


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, nx: bool = False, **_kwargs):
        if not nx or key not in self.values:
            self.values[key] = value

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def incr(self, key: str):
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value

    async def eval(self, script: str, _numkeys: int, *args):
        if "INCR" in script:
            version_key, cache_key = args
            await self.incr(version_key)
            await self.delete(cache_key)
            return 1
        version_key, cache_key, version, value, _ttl = args
        if self.values.get(version_key, "0") == str(version):
            self.values[cache_key] = value
            return "OK"
        return None


@pytest_asyncio.fixture
async def db_session():
    engine = create_utc_sqlite_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_session_reads_database_instead_of_shared_cache(db_session, monkeypatch):
    fake_redis = FakeRedis()
    cache_key = f"{options.OPTION_CACHE_PREFIX}{system_options.key}"
    fake_redis.values[cache_key] = json.dumps({"default_model": "cached:model"})
    monkeypatch.setattr(options, "get_async_redis_client", lambda: _async_value(fake_redis))
    await ensure_options_in_db(db_session)
    await options.update_option_value(
        db_session,
        system_options.key,
        {"default_model": "database:model"},
        "tester",
    )

    values = await system_options.get(db_session)

    assert values["default_model"] == "database:model"


@pytest.mark.asyncio
async def test_implicit_option_read_uses_shared_cache(monkeypatch):
    fake_redis = FakeRedis()
    cache_key = f"{options.OPTION_CACHE_PREFIX}{system_options.key}"
    fake_redis.values[cache_key] = json.dumps({"default_model": "cached:model"})
    monkeypatch.setattr(options, "get_async_redis_client", lambda: _async_value(fake_redis))

    values = await system_options.get()

    assert values["default_model"] == "cached:model"


@pytest.mark.asyncio
async def test_sensitive_option_does_not_use_redis(db_session, monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(options, "get_async_redis_client", lambda: _async_value(fake_redis))
    await ensure_options_in_db(db_session)
    await options.update_option_value(
        db_session,
        mineru_official_api_opts.key,
        {"api_key": "database-secret"},
        "tester",
    )

    values = await mineru_official_api_opts.get(db_session)

    assert values["api_key"] == "database-secret"
    assert fake_redis.values == {}


@pytest.mark.asyncio
async def test_invalidate_option_cache_removes_cached_value(monkeypatch):
    fake_redis = FakeRedis()
    key = f"{options.OPTION_CACHE_PREFIX}{system_options.key}"
    fake_redis.values[key] = "{}"
    monkeypatch.setattr(options, "get_async_redis_client", lambda: _async_value(fake_redis))

    await options.invalidate_option_cache(system_options.key)

    assert key not in fake_redis.values


@pytest.mark.asyncio
async def test_stale_database_read_does_not_refill_invalidated_cache(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(options, "get_async_redis_client", lambda: _async_value(fake_redis))
    version = await options._load_cache_version(system_options.key)

    await options.invalidate_option_cache(system_options.key)
    await options._save_cached_value(system_options.key, {"default_model": "stale:model"}, version)

    cache_key = f"{options.OPTION_CACHE_PREFIX}{system_options.key}"
    assert cache_key not in fake_redis.values


@pytest.mark.asyncio
async def test_first_implicit_option_read_initializes_cache_version(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(options, "get_async_redis_client", lambda: _async_value(fake_redis))

    version = await options._load_cache_version(system_options.key)
    await options._save_cached_value(system_options.key, {"default_model": "first:model"}, version)

    cache_key = f"{options.OPTION_CACHE_PREFIX}{system_options.key}"
    assert version == "0"
    assert json.loads(fake_redis.values[cache_key]) == {"default_model": "first:model"}


async def _async_value(value):
    return value


def test_cooperation_depth_defaults_to_two_and_preserves_zero():
    """整数配置的零值用于禁止新增子会话，不可被默认值覆盖。"""
    assert system_options.resolve({})["max_cooperation_depth"] == 2
    assert system_options.resolve({"max_cooperation_depth": 0})["max_cooperation_depth"] == 0


def test_cooperation_concurrency_defaults_to_four_and_preserves_one():
    """并发执行上限默认 4，允许降到 1 但不允许清零冻结整树。"""
    assert system_options.resolve({})["max_cooperation_concurrency"] == 4
    assert system_options.resolve({"max_cooperation_concurrency": 1})["max_cooperation_concurrency"] == 1


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "4", None])
def test_cooperation_concurrency_rejects_non_integer_or_below_one_values(value):
    """配置持久化边界拒绝错误数值，不把 0 当成合法上限。"""
    field = {"key": "max_cooperation_concurrency", "type": "integer", "min": 1}
    with pytest.raises(ValueError, match="不小于 1 的整数"):
        options.normalize_option_value(field, value)


@pytest.mark.parametrize("value", [-1, True, 1.5, "2", None])
def test_cooperation_depth_rejects_non_integer_or_negative_values(value):
    """配置持久化边界拒绝错误数值，不按字符串或布尔值凑成深度。"""
    field = {"key": "max_cooperation_depth", "type": "integer", "min": 0}
    with pytest.raises(ValueError, match="非负整数"):
        options.normalize_option_value(field, value)
