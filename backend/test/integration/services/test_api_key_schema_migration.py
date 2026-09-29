"""API Key 历史 schema 在真实 PostgreSQL 上的升级测试。"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _scoped_manager(engine) -> PostgresManager:
    """创建不触碰进程单例的隔离 schema manager。"""

    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    return manager


async def test_api_key_schema_upgrade_is_idempotent_and_preserves_safe_history() -> None:
    """旧表升级两次后应清理孤儿、保留历史并回填删除用户 tombstone。"""

    schema = f"pytest_api_key_migration_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        async with scoped_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("DROP TABLE cli_auth_sessions"))
            await connection.execute(text("DROP TABLE api_keys"))
            await connection.execute(
                text(
                    """
                    CREATE TABLE api_keys (
                        id SERIAL PRIMARY KEY,
                        key_hash VARCHAR(64) NOT NULL UNIQUE,
                        key_prefix VARCHAR(16) NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        user_id INTEGER REFERENCES users(id),
                        department_id INTEGER REFERENCES departments(id),
                        expires_at TIMESTAMP WITHOUT TIME ZONE,
                        is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        last_used_at TIMESTAMP WITHOUT TIME ZONE,
                        created_by VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE cli_auth_sessions (
                        id SERIAL PRIMARY KEY,
                        device_code_hash VARCHAR(64) NOT NULL UNIQUE,
                        user_code VARCHAR(16) NOT NULL UNIQUE,
                        status VARCHAR(32) NOT NULL,
                        key_name VARCHAR(100) NOT NULL,
                        approved_user_id INTEGER REFERENCES users(id),
                        api_key_id INTEGER REFERENCES api_keys(id),
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                        approved_at TIMESTAMP WITHOUT TIME ZONE,
                        consumed_at TIMESTAMP WITHOUT TIME ZONE
                    )
                    """
                )
            )
            active_user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (
                        username, uid, password_hash, role, login_failed_count, is_deleted
                    ) VALUES (
                        'migration active', 'migration_active', '$argon2id$placeholder', 'user', 0, 0
                    ) RETURNING id
                    """
                )
            )
            deleted_user_id = await connection.scalar(
                text(
                    """
                    INSERT INTO users (
                        username, uid, password_hash, role, login_failed_count, is_deleted, deleted_at
                    ) VALUES (
                        'migration deleted', 'migration_deleted', '$argon2id$placeholder', 'user', 0, 1,
                        TIMESTAMP '2024-01-02 03:04:05'
                    ) RETURNING id
                    """
                )
            )
            active_key_id = await connection.scalar(
                text(
                    """
                    INSERT INTO api_keys (key_hash, key_prefix, name, user_id, is_enabled, created_by)
                    VALUES ('active-hash', 'active-prefix', 'active key', :user_id, TRUE, 'migration')
                    RETURNING id
                    """
                ),
                {"user_id": active_user_id},
            )
            disabled_key_id = await connection.scalar(
                text(
                    """
                    INSERT INTO api_keys (key_hash, key_prefix, name, user_id, is_enabled, created_by)
                    VALUES ('disabled-hash', 'disabled-pref', 'disabled key', :user_id, FALSE, 'migration')
                    RETURNING id
                    """
                ),
                {"user_id": active_user_id},
            )
            deleted_key_id = await connection.scalar(
                text(
                    """
                    INSERT INTO api_keys (key_hash, key_prefix, name, user_id, is_enabled, created_by)
                    VALUES ('deleted-hash', 'deleted-pref', 'deleted user key', :user_id, FALSE, 'migration')
                    RETURNING id
                    """
                ),
                {"user_id": deleted_user_id},
            )
            orphan_key_id = await connection.scalar(
                text(
                    """
                    INSERT INTO api_keys (key_hash, key_prefix, name, user_id, is_enabled, created_by)
                    VALUES ('orphan-hash', 'orphan-prefix', 'orphan key', NULL, TRUE, 'migration')
                    RETURNING id
                    """
                )
            )
            orphan_session_id = await connection.scalar(
                text(
                    """
                    INSERT INTO cli_auth_sessions (
                        device_code_hash, user_code, status, key_name, approved_user_id, api_key_id, expires_at
                    ) VALUES (
                        'orphan-device', 'ORPH-AN01', 'consumed', 'orphan cli', :user_id, :api_key_id,
                        CURRENT_TIMESTAMP + INTERVAL '1 hour'
                    ) RETURNING id
                    """
                ),
                {"user_id": active_user_id, "api_key_id": orphan_key_id},
            )
            deleted_session_id = await connection.scalar(
                text(
                    """
                    INSERT INTO cli_auth_sessions (
                        device_code_hash, user_code, status, key_name, approved_user_id, api_key_id, expires_at
                    ) VALUES (
                        'deleted-device', 'DELE-TED1', 'consumed', 'deleted cli', :user_id, :api_key_id,
                        CURRENT_TIMESTAMP + INTERVAL '1 hour'
                    ) RETURNING id
                    """
                ),
                {"user_id": deleted_user_id, "api_key_id": deleted_key_id},
            )

        manager = _scoped_manager(scoped_engine)
        await manager.ensure_business_schema()
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            columns = {
                row.column_name: row.is_nullable
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT column_name, is_nullable
                            FROM information_schema.columns
                            WHERE table_schema = :schema AND table_name = 'api_keys'
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            indexes = {
                row.indexname: row.indexdef
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT indexname, indexdef
                            FROM pg_indexes
                            WHERE schemaname = :schema AND tablename = 'api_keys'
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            key_rows = {
                row.id: row
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT id, user_id, is_enabled, revoked_at, request_id, intent_hash,
                                   access_level, app_id
                            FROM api_keys
                            ORDER BY id
                            """
                        )
                    )
                )
            }
            session_rows = {
                row.id: row.api_key_id
                for row in (await connection.execute(text("SELECT id, api_key_id FROM cli_auth_sessions ORDER BY id")))
            }

        assert {"request_id", "intent_hash", "revoked_at", "access_level", "app_id"}.issubset(columns)
        assert columns["access_level"] == "NO"
        assert columns["user_id"] == "NO"
        assert "UNIQUE" in indexes["ix_api_keys_request_id"]
        assert "ix_api_keys_revoked_at" in indexes
        assert set(key_rows) == {active_key_id, disabled_key_id, deleted_key_id}
        assert key_rows[active_key_id].is_enabled is True
        assert key_rows[active_key_id].revoked_at is None
        assert key_rows[active_key_id].access_level == "full"
        assert key_rows[active_key_id].app_id is None
        assert key_rows[disabled_key_id].is_enabled is False
        assert key_rows[disabled_key_id].revoked_at is None
        assert key_rows[deleted_key_id].is_enabled is False
        assert str(key_rows[deleted_key_id].revoked_at) == "2024-01-02 03:04:05"
        assert session_rows[orphan_session_id] is None
        assert session_rows[deleted_session_id] == deleted_key_id

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("UPDATE api_keys SET request_id = 'historical-request' WHERE id = :id"),
                {"id": active_key_id},
            )
        with pytest.raises(IntegrityError):
            async with scoped_engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO api_keys (
                            key_hash, key_prefix, request_id, name, user_id, is_enabled, created_by
                        ) VALUES (
                            'duplicate-request-hash', 'duplicate-pref', 'historical-request', 'duplicate request',
                            :user_id, TRUE, 'migration'
                        )
                        """
                    ),
                    {"user_id": active_user_id},
                )
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
