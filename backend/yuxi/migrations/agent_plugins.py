"""离线审阅并转换 Skill 运行依赖，不改写历史输入或执行清单。"""

import argparse
import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import open_regular_file_fd
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import BUSINESS_SCHEMA_VERSION, get_schema_versions
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.migrations.schema import schema_migration_lock
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES
from yuxi.modules.extensions.skills.access import user_can_access_skill
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import (
    compute_skill_directory_hash,
    copy_skill_tree_no_symlinks,
    is_valid_skill_slug,
    load_skill_frontmatter,
    normalize_string_list,
    parse_skill_markdown,
    skill_tree_contains_symlink,
    split_skill_frontmatter,
    validated_shared_skill_parts,
)
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.paths import user_workspace_dir

DEPENDENCY_FIELDS = ("tool_dependencies", "mcp_dependencies", "skill_dependencies")
BUILTIN_DEPENDENT_SKILLS = {"image-gen", "deep-research", "knowledge-base"}


async def export_plugin_migration(manager: PostgresManager, destination: Path) -> dict:
    """导出待审阅声明和源快照，不修改业务数据。"""
    async with schema_migration_lock(manager):
        async with manager.async_engine.begin() as conn:
            plan = await _build_plan(conn)
    destination.mkdir(parents=True, exist_ok=False)
    declarations = destination / "deployed"
    declarations.mkdir()
    for item in plan["skills"]:
        if item["plugin"] is not None and item["source_type"] != "builtin":
            declaration = item["plugin"]
            (declarations / f"{declaration['plugin_id']}.json").write_text(_json(declaration) + "\n", encoding="utf-8")
    (destination / "plan.json").write_text(_json(plan) + "\n", encoding="utf-8")
    return plan


async def apply_plugin_migration(manager: PostgresManager, source: Path) -> None:
    """排空执行并核对已部署声明后，事务更新未来配置与 Schema。"""
    staged = []
    commit_started = False
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions.get("business") == BUSINESS_SCHEMA_VERSION:
            return
        reviewed = json.loads((source / "plan.json").read_text(encoding="utf-8"))
        try:
            async with manager.async_engine.begin() as conn:
                # 阻止迁移期间提交新输入、执行或配置；服务仍必须先离线。
                await conn.execute(
                    text("LOCK TABLE agent_turns, agent_runs, agent_inputs, agents, sessions, skills IN ACCESS EXCLUSIVE MODE")
                )
                current = await _build_plan(conn)
                if current != reviewed:
                    raise ValueError("源数据或内容已变化，请重新导出并审阅迁移")
                _validate_deployed(current)
                for item in current["skills"]:
                    old = get_skill_data_dir() / item["dir_path"]
                    target = get_skill_data_dir() / "packages" / item["slug"] / uuid.uuid4().hex
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staged.append(target)
                    copy_skill_tree_no_symlinks(old, target)
                    root = target / "SKILL.md"
                    frontmatter, body = split_skill_frontmatter(root.read_text(encoding="utf-8"))
                    metadata = load_skill_frontmatter(frontmatter)
                    for key in DEPENDENCY_FIELDS:
                        metadata.pop(key, None)
                    root.write_text(
                        "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + body, encoding="utf-8"
                    )
                    await conn.execute(
                        text("UPDATE skills SET dir_path=:path, content_hash=:hash WHERE id=:id"),
                        {
                            "path": target.relative_to(get_skill_data_dir()).as_posix(),
                            "hash": compute_skill_directory_hash(target).hex(),
                            "id": item["id"],
                        },
                    )
                for change in current["configs"]:
                    table, column = ("agents", "config_json") if change["kind"] == "agent" else ("sessions", "config_snapshot")
                    await conn.execute(
                        text(f"UPDATE {table} SET {column}=CAST(:config AS JSON) WHERE id=:id"),
                        {"config": _json(change["after"]), "id": change["id"]},
                    )
                for column in DEPENDENCY_FIELDS:
                    await conn.execute(text(f"ALTER TABLE skills DROP COLUMN {column}"))
                await conn.execute(
                    text("UPDATE yuxi_schema_migrations SET version=:version, applied_at=CURRENT_TIMESTAMP WHERE domain='business'"),
                    {"version": BUSINESS_SCHEMA_VERSION},
                )
                # 提交响应可能丢失；进入提交阶段后保留新包，避免删除数据库已经引用的文件。
                commit_started = True
        finally:
            if not commit_started:
                for path in staged:
                    shutil.rmtree(path, ignore_errors=True)


async def _build_plan(conn) -> dict:
    """读取旧域事实、文件摘要和按当前可见性计算的配置变更。"""
    version = await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'"))
    if version != 2:
        raise ValueError("插件迁移需要 business v2")
    active = await conn.scalar(
        text("SELECT count(*) FROM agent_runs WHERE status <> ALL(:terminal)"), {"terminal": list(AGENT_RUN_TERMINAL_STATUSES)}
    )
    pending = await conn.scalar(text("SELECT count(*) FROM agent_inputs WHERE status='pending'"))
    resumable = await conn.scalar(text("SELECT count(*) FROM agent_turns WHERE status IN ('running', 'waiting', 'cancelling')"))
    if active or pending or resumable:
        raise ValueError("请先排空非终态 Run、待消费 Input 和可恢复 Turn，再执行插件迁移")
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT id,slug,name,description,source_type,dir_path,tool_dependencies,mcp_dependencies,skill_dependencies "
                    "FROM skills ORDER BY id"
                )
            )
        )
        .mappings()
        .all()
    )
    items = []
    by_slug = {row["slug"]: row for row in rows}
    for row in rows:
        validated_shared_skill_parts(row["slug"], row["dir_path"])
        path = get_skill_data_dir() / row["dir_path"] / "SKILL.md"
        metadata = load_skill_frontmatter(split_skill_frontmatter(path.read_text(encoding="utf-8"))[0])
        has_dependencies = any(row[key] for key in DEPENDENCY_FIELDS)
        if not has_dependencies and not any(key in metadata for key in DEPENDENCY_FIELDS):
            continue
        for key in DEPENDENCY_FIELDS:
            if metadata.get(key, []) != row[key]:
                raise ValueError(f"Skill 文件与数据库依赖不一致：{row['slug']}.{key}")
        item = dict(row)
        item["directory_hash"] = compute_skill_directory_hash(get_skill_data_dir() / row["dir_path"]).hex()
        closure = []
        pending = [row["slug"]]
        while pending:
            slug = pending.pop(0)
            if slug in closure:
                continue
            if slug not in by_slug:
                raise ValueError(f"Skill 依赖已缺失：{slug}")
            closure.append(slug)
            pending.extend(by_slug[slug]["skill_dependencies"])
        item["plugin"] = {
            "plugin_id": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "entry_skill": row["slug"],
            "skills": closure,
            "mcps": list(dict.fromkeys(mcp for slug in closure for mcp in by_slug[slug]["mcp_dependencies"])),
        }
        if (row["source_type"] == "builtin" and row["slug"] in {"image-gen", "deep-research"}) or (
            not item["plugin"]["mcps"] and len(closure) == 1
        ):
            item["plugin"] = None
        if has_dependencies and row["source_type"] == "builtin" and row["slug"] not in BUILTIN_DEPENDENT_SKILLS:
            raise ValueError(f"未知内置依赖 Skill：{row['slug']}")
        items.append(item)
    configs = []
    async with AsyncSession(bind=conn) as session:
        users = {user.uid: user for user in (await session.scalars(select(User))).all()}
        require_clean_personal_skills(list(users))
        skills = {skill.slug: skill for skill in (await session.scalars(select(Skill))).all()}
        for table, column, owner, kind in (
            ("agents", "config_json", "created_by", "agent"),
            ("sessions", "config_snapshot", "uid", "session"),
        ):
            agent_column = "slug" if kind == "agent" else "agent_id"
            query = f"SELECT id,{column} AS config,{owner} AS owner,{agent_column} AS agent_slug FROM {table} ORDER BY id"
            for row in (await conn.execute(text(query))).mappings():
                if row["config"] is None:
                    continue
                user = users.get(row["owner"])
                allowed = [
                    item["slug"]
                    for item in items
                    if item["plugin"] is not None
                    and skills[item["slug"]].enabled
                    and (kind == "agent" or (user is not None and user_can_access_skill(user, skills[item["slug"]])))
                    and (skills[item["slug"]].bound_agent_id is None or skills[item["slug"]].bound_agent.slug == row["agent_slug"])
                ]
                bound = [slug for slug in allowed if skills[slug].bound_agent_id is not None]
                original = row["config"]
                context = (original.get("context") or {}) if kind == "agent" else original
                converted = convert_config(
                    context,
                    allowed,
                    bound=bound,
                    migrated=[item["slug"] for item in items if item["plugin"]],
                )
                after = {**original, "context": converted} if kind == "agent" else converted
                if after != row["config"]:
                    configs.append({"kind": kind, "id": row["id"], "before": row["config"], "after": after})
    return {"version": 1, "skills": items, "configs": configs}


def require_clean_personal_skills(uids: list[str]) -> None:
    """旧个人依赖从未授予能力，升级前要求显式清除而非转换授权。"""
    for uid in uids:
        root = user_workspace_dir(uid) / "agents" / "skills"
        if not root.exists():
            continue
        for directory in sorted(root.iterdir()):
            if directory.is_symlink() or not directory.is_dir() or not is_valid_skill_slug(directory.name):
                continue
            if skill_tree_contains_symlink(directory):
                continue
            path = directory / "SKILL.md"
            if not path.exists():
                continue
            try:
                with open_regular_file_fd(Path(path.anchor), path.parts[1:]) as (fd, _):
                    with os.fdopen(os.dup(fd), encoding="utf-8") as stream:
                        frontmatter, _ = split_skill_frontmatter(stream.read())
                metadata = load_skill_frontmatter(frontmatter)
                if any(
                    not isinstance(metadata.get(key, []), list)
                    or any(not isinstance(value, str) for value in metadata[key])
                    or normalize_string_list(metadata[key]) != metadata[key]
                    for key in DEPENDENCY_FIELDS
                    if key in metadata
                ):
                    continue
                fields = [key for key in DEPENDENCY_FIELDS if key in metadata]
                plain = {key: value for key, value in metadata.items() if key not in DEPENDENCY_FIELDS}
                slug, _, _, _ = parse_skill_markdown("---\n" + yaml.safe_dump(plain) + "---\n")
                if slug != directory.name:
                    continue
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                # 这些文件原本就不会进入个人 Skill 目录；迁移不扩大修复范围。
                continue
            if fields:
                raise ValueError(
                    f"个人 Skill 的旧依赖从未生效，不会转换为插件。请审阅并删除 {path} 中的字段 "
                    f"{', '.join(fields)}（包括空列表），然后重新导出迁移；正文和脚本无需删除。"
                )


def convert_config(
    config: dict,
    allowed: list[str],
    *,
    bound: list[str] | None = None,
    migrated: list[str] | None = None,
) -> dict:
    """保留独立资源选择，将既有入口选择转换为显式插件列表。"""
    result = dict(config)
    selected = config.get("skills", "all")
    chosen = allowed if selected == "all" else [slug for slug in allowed if slug in selected]
    chosen = list(dict.fromkeys([*chosen, *(bound or [])]))
    plugin_slugs = allowed if migrated is None else migrated
    chosen_plugins = [slug for slug in chosen if slug in plugin_slugs]
    if chosen_plugins:
        result["plugins"] = list(dict.fromkeys([*config.get("plugins", []), *chosen_plugins]))
    for field in ("skills", "preload_skills"):
        values = result.get(field)
        if isinstance(values, list):
            result[field] = [slug for slug in values if slug not in plugin_slugs]
    mcps = config.get("mcps", [])
    if mcps == "all" or "abridge" in mcps:
        result["plugins"] = list(dict.fromkeys([*result.get("plugins", []), "abridge"]))
        if isinstance(mcps, list):
            result["mcps"] = [slug for slug in mcps if slug != "abridge"]
    return result


def _validate_deployed(plan: dict) -> None:
    """已审阅的包必须随当前部署可用，内置资源也需核对。"""
    from yuxi.modules.extensions.plugins.registry import get_plugin, get_plugins

    get_plugins.cache_clear()
    for item in plan["skills"]:
        expected = item["plugin"]
        if expected is None:
            continue
        plugin = get_plugin(expected["plugin_id"])
        for field in ("entry_skill", "skills", "mcps"):
            actual = getattr(plugin, field)
            if isinstance(actual, tuple):
                actual = list(actual)
            if actual != expected[field]:
                raise ValueError(f"已部署插件与审阅声明不匹配：{expected['plugin_id']}.{field}")
        if item["source_type"] != "builtin" and (plugin.name != expected["name"] or plugin.description != expected["description"]):
            raise ValueError("已部署插件名称或说明与审阅声明不匹配")


def _json(value: dict) -> str:
    """输出稳定、可供人工审阅的 JSON。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


async def main() -> None:
    """显式执行导出或已审阅应用，不在应用启动时运行。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "apply"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    load_models()
    manager = PostgresManager()
    manager.initialize()
    try:
        if args.action == "export":
            await export_plugin_migration(manager, args.directory)
        else:
            await apply_plugin_migration(manager, args.directory)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
