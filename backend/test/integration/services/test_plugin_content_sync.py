"""插件源码 Skill 通过真实同步保持共享索引身份与文件内容。"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.extensions.plugins import registry
from yuxi.modules.extensions.skills.builtin import sync
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import compute_skill_directory_hash

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_plugin_bundled_skills_sync_preserves_shared_identity_and_disabled_state(tmp_path, monkeypatch):
    """实际包内文件落库落盘，重复同步保留原 ID、共享路径与停用状态。"""
    shared_root = tmp_path / "skill-data" / "shared"
    shared_root.mkdir(parents=True)
    monkeypatch.setattr(sync, "get_skills_root_dir", lambda: shared_root)
    specs = {spec["slug"]: spec for spec in sync.list_builtin_skill_specs()}
    plugin_root = Path(registry.__file__).parent / "builtin"
    expected_sources = {
        "knowledge-base": plugin_root / "knowledge" / "skills" / "knowledge-base",
        "image-gen": sync.BUILTIN_SKILLS_DIR / "image-gen",
    }
    for slug, source in expected_sources.items():
        assert specs[slug]["source_dir"] == source

    schema, admin, engine, _manager = await create_isolated_manager("pytest_plugin_content")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Agent.__table__.create)
            await connection.run_sync(Skill.__table__.create)
        async with sessions() as db:
            await sync.init_builtin_skills(db)

        original_ids = {}
        async with sessions() as db:
            rows = list(await db.scalars(select(Skill).where(Skill.slug.in_(expected_sources))))
            assert {row.slug for row in rows} == set(expected_sources)
            for row in rows:
                original_ids[row.slug] = row.id
                assert row.source_type == "builtin"
                assert row.dir_path == f"shared/{row.slug}"
                assert row.share_config == {
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                }
                assert row.enabled is True
                source = expected_sources[row.slug]
                target = shared_root / row.slug
                source_files = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
                target_files = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
                assert target_files == source_files
                assert row.content_hash == compute_skill_directory_hash(target).hex() == specs[row.slug]["content_hash"]
                row.enabled = False
            await db.commit()

        async with sessions() as db:
            await sync.init_builtin_skills(db)

        async with sessions() as db:
            rows = list(await db.scalars(select(Skill).where(Skill.slug.in_(expected_sources))))
            assert {row.slug: row.id for row in rows} == original_ids
            for row in rows:
                assert row.enabled is False
                assert row.source_type == "builtin"
                assert row.dir_path == f"shared/{row.slug}"
                assert row.share_config == {
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                }
                assert (shared_root / row.slug / "SKILL.md").read_bytes() == (expected_sources[row.slug] / "SKILL.md").read_bytes()
                assert row.content_hash == compute_skill_directory_hash(shared_root / row.slug).hex()
    finally:
        await drop_isolated_schema(schema, admin, engine)
