from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.config import options
from yuxi.storage.postgres.models_business import Base


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_options_syncs_definitions_and_preserves_values(db_session):
    records = await options.ensure_options_in_db(db_session)
    records[0].value = {"server_url": "http://custom-mineru:30001"}
    await db_session.flush()

    synced = await options.ensure_options_in_db(db_session)

    assert [record.key for record in synced] == list(options.OPTION_DEFINITIONS)
    assert synced[0].value == {"server_url": "http://custom-mineru:30001"}


@pytest.mark.asyncio
async def test_secret_is_stored_plaintext_but_not_serialized(db_session, monkeypatch):
    await options.ensure_options_in_db(db_session)
    monkeypatch.delenv("MINERU_API_KEY", raising=False)

    record = await options.update_option_value(
        db_session,
        "mineru_official_api_opts",
        {"api_key": "database-secret"},
        "tester",
    )

    assert record.value["api_key"] == "database-secret"
    serialized = options.serialize_option(record)
    assert serialized["value"]["api_key"] == ""
    assert serialized["sensitive_configured"]["api_key"] is True
    assert serialized["sensitive_state"]["api_key"] == {
        "source": "database",
        "configured": True,
        "preview": "da*******et",
    }
    assert "database-secret" not in str(serialized)


@pytest.mark.asyncio
async def test_empty_secret_clears_database_value_and_falls_back_to_environment(db_session, monkeypatch):
    await options.ensure_options_in_db(db_session)
    monkeypatch.setenv("MINERU_API_KEY", "environment-secret")
    await options.update_option_value(
        db_session,
        "mineru_official_api_opts",
        {"api_key": "database-secret"},
        "tester",
    )

    record = await options.update_option_value(
        db_session,
        "mineru_official_api_opts",
        {"api_key": ""},
        "tester",
    )
    effective = await options.mineru_official_api_opts.get(db_session)

    assert record.value["api_key"] == ""
    assert effective["api_key"] == "environment-secret"
    assert options.serialize_option(record)["sensitive_state"]["api_key"] == {
        "source": "environment",
        "configured": True,
        "preview": None,
    }


@pytest.mark.asyncio
async def test_sensitive_state_reports_unconfigured_without_database_or_environment(db_session, monkeypatch):
    records = await options.ensure_options_in_db(db_session)
    monkeypatch.delenv("MINERU_API_KEY", raising=False)
    record = next(item for item in records if item.key == "mineru_official_api_opts")

    assert options.serialize_option(record)["sensitive_state"]["api_key"] == {
        "source": "none",
        "configured": False,
        "preview": None,
    }


@pytest.mark.asyncio
async def test_empty_value_falls_back_to_environment(db_session, monkeypatch):
    await options.ensure_options_in_db(db_session)
    monkeypatch.setenv("PADDLEX_URI", "http://paddlex-env:8080")

    effective = await options.pp_structure_v3_ocr_host_opts.get(db_session)

    assert effective["server_url"] == "http://paddlex-env:8080"


@pytest.mark.asyncio
async def test_option_get_queries_database_each_time(db_session, monkeypatch):
    await options.ensure_options_in_db(db_session)
    real_get_option = options.get_option
    query_count = 0

    async def counted_get_option(db, key):
        nonlocal query_count
        query_count += 1
        return await real_get_option(db, key)

    monkeypatch.setattr(options, "get_option", counted_get_option)
    await options.update_option_value(
        db_session,
        "mineru_ocr_host_opts",
        {"server_url": "http://first-mineru:30001"},
        "tester",
    )
    first = await options.mineru_ocr_host_opts.get(db_session)

    await options.update_option_value(
        db_session,
        "mineru_ocr_host_opts",
        {"server_url": "http://second-mineru:30001"},
        "tester",
    )
    second = await options.mineru_ocr_host_opts.get(db_session)

    assert first["server_url"] == "http://first-mineru:30001/"
    assert second["server_url"] == "http://second-mineru:30001/"
    assert query_count == 4


def test_sensitive_fields_use_explicit_metadata():
    fields = options.mineru_official_api_opts.params["fields"]

    assert fields[0]["sensitive"] is True


@pytest.mark.asyncio
async def test_update_rejects_unknown_fields_and_invalid_urls(db_session):
    await options.ensure_options_in_db(db_session)

    with pytest.raises(ValueError, match="未知配置字段"):
        await options.update_option_value(db_session, "mineru_ocr_host_opts", {"unknown": "x"}, "tester")
    with pytest.raises(ValueError):
        await options.update_option_value(db_session, "mineru_ocr_host_opts", {"server_url": "not-url"}, "tester")
