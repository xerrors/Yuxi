"""插件源码 Skill 通过真实同步保持共享索引身份与文件内容。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.module_audit import isolated_audit_store
from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.extensions.plugins import registry
from yuxi.modules.extensions.skills import projection
from yuxi.modules.extensions.skills.builtin import sync
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import compute_skill_directory_hash
from yuxi.modules.identity.models import User

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


async def test_plugin_private_skill_uses_entry_permission_and_projection(tmp_path, monkeypatch):
    """配套 Skill 的文件同入口一起发布、授权和撤下，目录没有第二个可选条目。"""
    entry = tmp_path / "plugin/skills/guide"
    helper = Path("skills/word-count/SKILL.md")
    (entry / helper).parent.mkdir(parents=True)
    (entry / helper).write_text("# 分词规则", encoding="utf-8")
    (entry / "SKILL.md").write_text("---\nname: guide\ndescription: 入口\n---\n[分词规则](skills/word-count/SKILL.md)", encoding="utf-8")

    definition = SimpleNamespace(plugin_id="word-metrics", entry_skill="guide", skills=("guide",))
    monkeypatch.setattr(registry, "REGISTERED_PLUGINS", (definition,))
    monkeypatch.setattr(registry, "inspect", SimpleNamespace(getfile=lambda plugin: str(tmp_path / "plugin/plugin.py")))
    state = tmp_path / "data"
    (state / "shared").mkdir(parents=True)
    monkeypatch.setattr(sync, "get_skills_root_dir", lambda: state / "shared")
    monkeypatch.setattr(projection, "get_skill_data_dir", lambda: state)
    monkeypatch.setattr(projection, "get_skill_projection_dir", lambda: tmp_path / "projections")
    uid = "plugin-reader"

    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            db.add(User(uid=uid, username=uid, password_hash="unused", role="user"))
            await sync.init_builtin_skills(db)
            assert await db.scalar(select(Skill).where(Skill.slug == "word-count")) is None
        await projection.refresh_user_skill_projection_async(uid)
        projected = projection.get_user_skills_root_dir(uid)
        assert (projected / "guide" / helper).read_text(encoding="utf-8") == "# 分词规则"
        assert not (projected / "word-count").exists()

        async with sessions() as db:
            entry_row = await db.scalar(select(Skill).where(Skill.slug == "guide").with_for_update())
            entry_row.enabled = False
            await projection.commit_skill_policy_and_refresh_projections(db, "guide")
        assert not (projected / "guide").exists()
