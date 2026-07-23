from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.config.options import ensure_options_in_db, update_option_value
from yuxi.services import ocr_service
from yuxi.storage.postgres.models_business import Base, ModelProvider


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await ensure_options_in_db(session)
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_task_resolution_uses_database_option(db_session):
    await update_option_value(
        db_session,
        "mineru_ocr_host_opts",
        {"server_url": "http://mineru-config:30001"},
        "tester",
    )

    resolved = await ocr_service.resolve_ocr_task_params({"ocr_engine": "mineru_ocr"}, db_session)

    assert resolved["_ocr_processor_kwargs"] == {"server_url": "http://mineru-config:30001/"}


def test_resolve_ocr_engine_id_accepts_disable():
    assert ocr_service.resolve_ocr_engine_id("disable") == "disable"


def test_ocr_options_use_parser_metadata():
    options = ocr_service.get_ocr_options()
    rapid_ocr = next(item for item in options["engines"] if item["engine_id"] == "rapid_ocr")

    assert rapid_ocr["service_name"] == "rapid_ocr"
    assert rapid_ocr["display_name"] == "RapidOCR (ONNX)"
    assert ".pdf" in rapid_ocr["supported_extensions"]


@pytest.mark.asyncio
async def test_deepseek_uses_provider_credentials_without_chat_models(db_session):
    provider = ModelProvider(
        provider_id="siliconflow-cn",
        display_name="SiliconFlow",
        provider_type="openai",
        base_url="https://provider.example/v1",
        is_enabled=True,
        api_key="provider-secret",
        api_key_env=None,
        capabilities=["embedding"],
        enabled_models=[{"id": "BAAI/bge-m3", "type": "embedding"}],
    )
    db_session.add(provider)
    await db_session.flush()

    resolved = await ocr_service.resolve_ocr_task_params({"ocr_engine": "deepseek_ocr"}, db_session)

    assert resolved["_ocr_processor_kwargs"] == {
        "api_key": "provider-secret",
        "api_url": "https://provider.example/v1/chat/completions",
    }


@pytest.mark.asyncio
async def test_health_checks_every_registered_ocr_method(db_session, monkeypatch):
    async def build_kwargs(db, engine_id):
        del db
        return {"engine": engine_id}

    monkeypatch.setattr(ocr_service, "_build_processor_kwargs", build_kwargs)
    monkeypatch.setattr(
        ocr_service.DocumentProcessorFactory,
        "check_health",
        lambda engine_id, **kwargs: {"status": "healthy", "message": kwargs["engine"]},
    )

    health = await ocr_service.check_all_ocr_health(db_session)

    assert set(health) == set(ocr_service.PROCESSOR_TYPES)
    assert all(result["status"] == "healthy" for result in health.values())
