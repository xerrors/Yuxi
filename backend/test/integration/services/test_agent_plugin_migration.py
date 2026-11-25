"""部署插件升级在隔离 PostgreSQL Schema 上验证持久结果。"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.schema import get_schema_versions, require_current_schema
from yuxi.migrations.agent_plugins import apply_plugin_migration, export_plugin_migration
from yuxi.migrations.schema import create_business_tables, create_knowledge_tables, create_schema_version_table, record_schema_version

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def create_legacy_schema(manager):
    """用当前表构造迁移路径涉及的 v0.8.0 结构和版本。"""
    await create_business_tables(manager)
    await create_knowledge_tables(manager)
    await create_schema_version_table(manager)
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("ALTER TABLE sessions DROP COLUMN agent_state_view"))
        await conn.execute(text("ALTER TABLE mcp_servers DROP COLUMN code_owned"))
        for field in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
            await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))
    await record_schema_version(manager, "business", 2)
    await record_schema_version(manager, "knowledge", 2)


async def assert_legacy_schema(conn):
    """拒绝迁移后回读源版本及旧连接表结构。"""
    assert dict((await conn.execute(text("SELECT domain,version FROM yuxi_schema_migrations"))).all()) == {"business": 2, "knowledge": 2}
    assert not await conn.scalar(
        text("""SELECT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name='mcp_servers' AND column_name='code_owned')""")
    )


@pytest.mark.parametrize(
    "versions",
    [
        pytest.param({}, id="empty"),
        pytest.param({"business": 1, "knowledge": 2}, id="older-business"),
        pytest.param({"business": 4, "knowledge": 2}, id="unreleased-business"),
        pytest.param({"business": 2}, id="source-missing-knowledge"),
        pytest.param({"business": 2, "knowledge": 1}, id="source-older-knowledge"),
        pytest.param({"business": 2, "knowledge": 2, "initializing": 1}, id="initializing-source"),
        pytest.param({"business": 5}, id="target-missing-knowledge"),
        pytest.param({"business": 5, "knowledge": 1}, id="target-older-knowledge"),
        pytest.param({"business": 5, "knowledge": 2, "extra": 1}, id="target-extra-domain"),
    ],
)
async def test_plugin_migration_rejects_unsupported_schema_versions(tmp_path, versions):
    """混合或未发布版本在读取计划和写入前失败，源配置与表结构保持原样。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_version_guard")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO mcp_servers(slug,name,transport,enabled,auth_mode,created_by,updated_by)
                VALUES ('knowledge','Knowledge','streamable_http',1,'delegation','system','system')""")
            )
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('demo','chatbot','Demo','[]','{"context":{"mcps":["knowledge"]}}','{}','shared',false,false)""")
            )
            await conn.execute(text("DELETE FROM yuxi_schema_migrations"))
            for domain, version in versions.items():
                await conn.execute(
                    text("INSERT INTO yuxi_schema_migrations(domain,version) VALUES (:domain,:version)"),
                    {"domain": domain, "version": version},
                )
        review = tmp_path / "review"
        error = r"插件迁移仅支持 v0\.8\.0"
        with pytest.raises(ValueError, match=error):
            await export_plugin_migration(manager, review)
        assert not review.exists()
        with pytest.raises(ValueError, match=error):
            await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert dict((await conn.execute(text("SELECT domain,version FROM yuxi_schema_migrations"))).all()) == versions
            assert await conn.scalar(text("SELECT config_json FROM agents")) == {"context": {"mcps": ["knowledge"]}}
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                    "AND table_name IN ('skills','mcp_servers')"
                )
            )
            columns = set(rows.scalars())
            assert {"tool_dependencies", "mcp_dependencies", "skill_dependencies"} <= columns
            assert "code_owned" not in columns
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize(
    "legacy_servers,mcps,expected",
    [
        ([], "all", {"mcps": "all"}),
        (["knowledge"], "all", {"mcps": "all", "plugins": ["knowledge-base"]}),
        (["knowledge"], ["abridge", "reports"], {"mcps": ["reports"]}),
        (["knowledge", "abridge"], "all", None),
        (["abridge"], [" abridge ", "abridge ", " reports ", "reports"], None),
        (["knowledge"], [" knowledge ", "knowledge ", "reports", " reports "], {"mcps": ["reports"], "plugins": ["knowledge-base"]}),
        (["abridge"], [], {"mcps": []}),
    ],
)
async def test_plugin_migration_converts_confirmed_old_mcp_selections(tmp_path, monkeypatch, legacy_servers, mcps, expected):
    """v2 只转换旧库已有的能力，非标准设备连接须从旧清单确认。"""
    from yuxi.migrations import agent_plugins

    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_first_upgrade")
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / uid)
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            for slug in legacy_servers:
                await conn.execute(
                    text("""INSERT INTO mcp_servers(slug,name,transport,url,enabled,created_by,updated_by,auth_mode)
                    VALUES (:slug,:slug,'streamable_http',:url,0,'system','admin',:mode)"""),
                    {"slug": slug, "url": f"https://{slug}.example/mcp", "mode": "delegation" if slug == "knowledge" else "headers"},
                )
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('device','chatbot','Device','[]',CAST(:config AS JSON),'{}','shared',false,false)"""),
                {"config": json.dumps({"system_prompt": "kept", "context": {"mcps": mcps}})},
            )
        review = tmp_path / "review"
        if "abridge" in legacy_servers:
            # v2 允许名为 system 的用户创建普通 abridge，单凭用户名不能认领。
            with pytest.raises(ValueError, match="abridge.*来源冲突"):
                await export_plugin_migration(manager, tmp_path / "unconfirmed")
            async with engine.connect() as conn:
                assert (await conn.execute(text("SELECT url,enabled FROM mcp_servers WHERE slug='abridge'"))).one() == (
                    "https://abridge.example/mcp",
                    0,
                )
                await assert_legacy_schema(conn)
        if expected is None:
            with pytest.raises(ValueError, match="agents id=.*abridge.*升级后"):
                await export_plugin_migration(manager, review, legacy_builtins=("abridge",))
            async with engine.connect() as conn:
                await assert_legacy_schema(conn)
                assert (await conn.scalar(text("SELECT config_json FROM agents")))["context"] == {"mcps": mcps}
            return
        await export_plugin_migration(manager, review, legacy_builtins=("abridge",) if "abridge" in legacy_servers else ())
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='device'")) == {
                "system_prompt": "kept",
                "context": expected,
            }
            assert dict((await conn.execute(text("SELECT domain,version FROM yuxi_schema_migrations"))).all()) == {
                "business": 5,
                "knowledge": 2,
            }
        if mcps != "all" or legacy_servers != ["knowledge"]:
            return
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE agents SET config_json=CAST(:config AS JSON) WHERE slug='device'"),
                {"config": json.dumps({"context": {"mcps": [], "plugins": []}})},
            )
        await apply_plugin_migration(manager, tmp_path / "missing-review")
        with pytest.raises(ValueError, match=r"插件迁移仅支持 v0\.8\.0"):
            await export_plugin_migration(manager, tmp_path / "target")
        assert not (tmp_path / "target").exists()
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='device'")) == {"context": {"mcps": [], "plugins": []}}
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("slug", ["knowledge", "abridge", "deepwiki-official"])
async def test_legacy_builtin_headers_require_explicit_provenance(tmp_path, slug):
    """旧普通连接与清单同名时，只有旧清单的显式确认能改变其持久归属。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_legacy_builtin_auth")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO mcp_servers(slug,name,transport,auth_mode,enabled,disabled_tools,created_by,updated_by)
                VALUES (:slug,'Legacy','streamable_http','headers',0,'["read"]','system','admin')"""),
                {"slug": slug},
            )
        with pytest.raises(ValueError, match=rf"{slug}.*来源冲突"):
            await export_plugin_migration(manager, tmp_path / "unconfirmed")
        async with engine.connect() as conn:
            await assert_legacy_schema(conn)
            state = await conn.execute(text("SELECT auth_mode, enabled, disabled_tools FROM mcp_servers"))
            assert state.one() == ("headers", 0, ["read"])
        review = tmp_path / "confirmed"
        plan = await export_plugin_migration(manager, review, legacy_builtins=(slug,))
        assert plan["confirmed_legacy_builtins"] == [slug]
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT id,code_owned,auth_mode,enabled,disabled_tools FROM mcp_servers"))).one() == (
                plan["legacy_builtin_mcps"][slug],
                True,
                "headers",
                0,
                ["read"],
            )
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_plugin_migration_converts_session_knowledge_without_claiming_other_connections(tmp_path):
    """会话保留普通连接和知识库选择，只有可确认的旧知识连接转为插件。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_session_upgrade")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO mcp_servers(slug,name,transport,enabled,created_by,updated_by,auth_mode) VALUES
                ('knowledge','KB','streamable_http',0,'system','admin','delegation'),
                ('deepwiki-official','Wiki','streamable_http',1,'system','admin','headers'),
                ('custom','Other','streamable_http',1,'system','system','headers')""")
            )
            await create_legacy_project(conn, "owner")
            await conn.execute(
                text("""INSERT INTO sessions(thread_id,tree_root_thread_id,uid,agent_id,project_id,is_pinned,config_snapshot)
                VALUES ('thread','thread','owner','demo','project',false,
                '{"mcps":["custom","deepwiki-official","knowledge"],"knowledges":["kb"]}')""")
            )
        review = tmp_path / "review"
        await export_plugin_migration(manager, review, legacy_builtins=("deepwiki-official",))
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE mcp_servers SET auth_mode='headers' WHERE slug='knowledge'"))
        with pytest.raises(ValueError, match="knowledge.*来源冲突"):
            await apply_plugin_migration(manager, review)
        async with engine.begin() as conn:
            await assert_legacy_schema(conn)
            assert await conn.scalar(text("SELECT config_snapshot FROM sessions")) == {
                "mcps": ["custom", "deepwiki-official", "knowledge"],
                "knowledges": ["kb"],
            }
            await conn.execute(text("UPDATE mcp_servers SET auth_mode='delegation' WHERE slug='knowledge'"))
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT config_snapshot FROM sessions")) == {
                "mcps": ["custom", "deepwiki-official"],
                "plugins": ["knowledge-base"],
                "knowledges": ["kb"],
            }
            assert (await conn.execute(text("SELECT slug, code_owned, enabled FROM mcp_servers ORDER BY slug"))).all() == [
                ("custom", False, 1),
                ("deepwiki-official", True, 1),
                ("knowledge", True, 0),
            ]
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_new_builtin_cannot_take_over_existing_uploaded_skill(tmp_path, monkeypatch):
    """即使配置未引用同名上传包，导出和已审阅的应用也须在旧库上拒绝。"""
    from yuxi.migrations import agent_plugins

    monkeypatch.setattr(agent_plugins, "get_skill_data_dir", lambda: tmp_path)
    schema, admin, engine, manager = await create_isolated_manager("pytest_builtin_collision")
    try:
        await create_legacy_schema(manager)
        review = tmp_path / "review"
        await export_plugin_migration(manager, review)

        relative_dir = f"packages/abridge/{'a' * 32}"
        source = tmp_path / relative_dir / "SKILL.md"
        source.parent.mkdir(parents=True)
        content = b"---\nname: abridge\ndescription: Old uploaded Skill\n---\nKeep this content.\n"
        source.write_bytes(content)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO skills(slug,name,description,source_type,dir_path,share_config,enabled)
                VALUES ('abridge','abridge','Old uploaded Skill','upload',:dir_path,'{}',true)"""),
                {"dir_path": relative_dir},
            )

        with pytest.raises(ValueError, match="abridge.*冲突"):
            await export_plugin_migration(manager, tmp_path / "conflict")
        with pytest.raises(ValueError, match="abridge.*冲突"):
            await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            await assert_legacy_schema(conn)
            assert (await conn.execute(text("SELECT source_type, dir_path FROM skills"))).one() == ("upload", relative_dir)
        assert source.read_bytes() == content
        assert not (tmp_path / "conflict").exists()
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_plugin_migration_rejects_direct_selection_of_new_plugin_entry(tmp_path, monkeypatch):
    """新插件采用既有 Skill 标识时，Agent 与已冻结会话都须先清理普通入口选择。"""
    from yuxi.migrations import agent_plugins
    from yuxi.modules.agents.runtime.context import BaseContext
    from yuxi.modules.extensions.plugins import registry
    from yuxi.modules.extensions.plugins.base import YuxiPlugin

    class ReportPlugin(YuxiPlugin):
        """把旧普通报表说明作为新插件入口。"""

        plugin_id = "report-plugin"
        name = description = "报表"
        entry_skill = "plain-reports"
        skills = ("plain-reports",)

    monkeypatch.setattr(registry, "REGISTERED_PLUGINS", (*registry.REGISTERED_PLUGINS, ReportPlugin))
    source = tmp_path / "shared" / "plain-reports"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: plain-reports\ndescription: 报表\n---\n报表说明\n")
    monkeypatch.setattr(agent_plugins, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / uid)
    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_skill_entry")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO skills(slug,name,description,source_type,dir_path,share_config,enabled)
                VALUES ('plain-reports','plain-reports','报表','upload','shared/plain-reports','{}',true)""")
            )
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('demo','chatbot','Demo','[]','{"context":{"skills":["plain-reports"]}}','{}','shared',false,false)""")
            )
            await create_legacy_project(conn, "owner")
            await conn.execute(
                text("""INSERT INTO sessions(thread_id,tree_root_thread_id,uid,agent_id,project_id,is_pinned,config_snapshot)
                VALUES ('thread','thread','owner','demo','project',false,'{"skills":"all","preload_skills":["plain-reports"]}')""")
            )
        review = tmp_path / "review"
        await export_plugin_migration(manager, review)
        with pytest.raises(ValueError, match=r"agents id=\d+.*skills.*插件入口"):
            await apply_plugin_migration(manager, review)
        async with engine.begin() as conn:
            await assert_legacy_schema(conn)
            assert await conn.scalar(text("SELECT config_json FROM agents")) == {"context": {"skills": ["plain-reports"]}}
            await conn.execute(text("""UPDATE agents SET config_json='{"context":{"skills":"all"}}'"""))
        with pytest.raises(ValueError, match=r"sessions id=\d+.*preload_skills.*插件入口"):
            await apply_plugin_migration(manager, review)
        async with engine.begin() as conn:
            await assert_legacy_schema(conn)
            assert (await conn.scalar(text("SELECT config_snapshot FROM sessions")))["preload_skills"] == ["plain-reports"]
            await conn.execute(text("""UPDATE sessions SET config_snapshot='{"skills":"all","preload_skills":[]}'"""))
        review = tmp_path / "corrected"
        await export_plugin_migration(manager, review)
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            runtime = BaseContext()
            runtime.update_config((await conn.scalar(text("SELECT config_json FROM agents")))["context"])
            runtime.update_config(await conn.scalar(text("SELECT config_snapshot FROM sessions")))
            assert runtime.skills == "all" and runtime.preload_skills == [] and runtime.plugins == []
    finally:
        registry.get_plugins.cache_clear()
        await drop_isolated_schema(schema, admin, engine)


@pytest.fixture
def report_plugins(monkeypatch):
    """安装接管旧连接的报表插件和仅发布新连接的插件。"""
    from yuxi.modules.extensions.mcp import builtin
    from yuxi.modules.extensions.mcp.config import BuiltinMCP
    from yuxi.modules.extensions.plugins import registry
    from yuxi.modules.extensions.plugins.base import YuxiPlugin

    class Reports(YuxiPlugin):
        """发布既有报表连接的业务插件。"""

        plugin_id = entry_skill = "report-plugin"
        name = description = "报表"
        skills = ("report-plugin",)
        mcps = (BuiltinMCP(slug="reports", manifest={"type": "http", "url": "https://reports.example/mcp"}),)

    class NewPlugin(YuxiPlugin):
        """接管已被旧管理员删除的连接标识。"""

        plugin_id = entry_skill = "new-report"
        name = description = "新报表"
        skills = ("new-report",)
        mcps = (BuiltinMCP(slug="removed", manifest={"type": "http", "url": "https://removed.example/mcp"}),)

    class DeployedReports(Reports):
        """部署插件可以继承业务基类的运行钩子。"""

    monkeypatch.setattr(registry, "REGISTERED_PLUGINS", (*registry.REGISTERED_PLUGINS, DeployedReports, NewPlugin))
    monkeypatch.setattr(builtin, "BUILTIN_MCP_SERVERS", builtin.builtin_mcp_servers())
    try:
        yield Reports
    finally:
        registry.get_plugins.cache_clear()


async def test_upgrade_claims_reviewed_custom_builtin_rows_and_rejects_collisions(tmp_path, report_plugins):
    """回读旧清单认领的行，覆盖保留、撤下、同名普通连接与审阅后行被替换。"""
    from yuxi.modules.agents.runtime.context import BaseContext

    schema, admin, engine, manager = await create_isolated_manager("pytest_legacy_mcp_owners")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO mcp_servers(slug,name,transport,enabled,disabled_tools,created_by,updated_by) VALUES
                ('reports','Reports','streamable_http',0,'["restricted"]','system','admin'),
                ('retired','Retired','sse',1,'[]','system','system'),
                ('custom','Custom','sse',1,'[]','system','system')""")
            )
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('demo','chatbot','Demo','[]','{"context":{"mcps":["reports","removed","custom"]}}','{}','shared',false,false)""")
            )
            await create_legacy_project(conn, "owner")
            await conn.execute(
                text("""INSERT INTO sessions(thread_id,tree_root_thread_id,uid,agent_id,project_id,is_pinned,config_snapshot)
                VALUES ('thread','thread','owner','demo','project',false,'{"mcps":["removed","custom"]}')""")
            )
        with pytest.raises(ValueError, match="reports.*来源冲突"):
            await export_plugin_migration(manager, tmp_path / "unconfirmed")
        confirmed = ("reports", "retired")
        with pytest.raises(ValueError, match="缺少系统同步行：missing"):
            await export_plugin_migration(manager, tmp_path / "missing", legacy_builtins=(*confirmed, "missing"))
        review = tmp_path / "review"
        plan = await export_plugin_migration(manager, review, legacy_builtins=confirmed)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO mcp_servers(slug,name,transport,enabled,created_by,updated_by)
                VALUES ('abridge','Other','sse',1,'admin','admin')""")
            )
        with pytest.raises(ValueError, match="abridge.*来源冲突"):
            await apply_plugin_migration(manager, review)
        with pytest.raises(ValueError, match="abridge.*来源冲突"):
            await export_plugin_migration(manager, tmp_path / "ordinary", legacy_builtins=(*confirmed, "abridge"))
        async with engine.begin() as conn:
            await assert_legacy_schema(conn)
            assert (await conn.scalar(text("SELECT config_json FROM agents")))["context"]["mcps"] == ["reports", "removed", "custom"]
            await conn.execute(text("DELETE FROM mcp_servers WHERE slug='abridge'"))
            await conn.execute(text("UPDATE mcp_servers SET id=id+100 WHERE slug='reports'"))
        with pytest.raises(ValueError, match="源数据或内容已变化"):
            await apply_plugin_migration(manager, review)
        latest = tmp_path / "latest"
        updated = await export_plugin_migration(manager, latest, legacy_builtins=confirmed)
        assert updated["legacy_builtin_mcps"]["reports"] != plan["legacy_builtin_mcps"]["reports"]
        await apply_plugin_migration(manager, latest)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT slug, code_owned FROM mcp_servers ORDER BY slug"))).all() == [
                ("custom", False),
                ("reports", True),
                ("retired", True),
            ]
            assert (await conn.execute(text("SELECT id, enabled, disabled_tools FROM mcp_servers WHERE slug='reports'"))).one() == (
                updated["legacy_builtin_mcps"]["reports"],
                0,
                ["restricted"],
            )
            assert await conn.scalar(text("SELECT config_json FROM agents")) == {
                "context": {"mcps": ["custom"], "plugins": ["report-plugin"]}
            }
            agent_context = BaseContext()
            agent_context.update_config((await conn.scalar(text("SELECT config_json FROM agents")))["context"])
            session_context = BaseContext()
            session_context.update_config(await conn.scalar(text("SELECT config_snapshot FROM sessions")))
            assert (agent_context.mcps, agent_context.plugins) == (["custom"], ["report-plugin"])
            assert (session_context.mcps, session_context.plugins) == (["custom"], [])
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize(
    "table,extra,malformed_field",
    [
        ("agents", "mcp", "plugins"),
        ("sessions", "tool", "plugins"),
        ("agents", "shared-skill", "mcps"),
        ("sessions", "inherited-hook", "mcps"),
    ],
)
async def test_direct_selection_does_not_authorize_a_larger_plugin(tmp_path, report_plugins, table, extra, malformed_field):
    """导出和应用都拒绝从单项直接选择扩大权限，已明确启用的插件可保留。"""
    from langchain_core.tools import tool
    from yuxi.modules.extensions.mcp.config import BuiltinMCP

    @tool
    def publish_report() -> str:
        """发布报表。"""
        return "published"

    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_scope")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO mcp_servers(slug,name,transport,enabled,created_by,updated_by)
                VALUES ('reports','Reports','sse',1,'system','system')""")
            )
            if table == "agents":
                column = "config_json"
                original = {"context": {"mcps": [" reports ", "reports "]}}
                statement = """INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                    VALUES ('demo','chatbot','Demo','[]',CAST(:config AS JSON),'{}','shared',false,false) RETURNING id"""
            else:
                column = "config_snapshot"
                original = {"mcps": "all"}
                await create_legacy_project(conn, "owner")
                statement = """INSERT INTO sessions(thread_id,tree_root_thread_id,uid,agent_id,project_id,is_pinned,config_snapshot)
                    VALUES ('thread','thread','owner','demo','project',false,CAST(:config AS JSON)) RETURNING id"""
            row_id = await conn.scalar(text(statement), {"config": json.dumps(original)})
        confirmed = ("reports",)
        review = tmp_path / "review"
        await export_plugin_migration(manager, review, legacy_builtins=confirmed)
        if extra == "mcp":
            report_plugins.mcps += (BuiltinMCP(slug="billing", manifest={"type": "http", "url": "https://billing.example/mcp"}),)
        elif extra == "tool":
            report_plugins.tools = (publish_report,)
        elif extra == "shared-skill":
            report_plugins.skills += ("chat-widgets",)
        else:

            async def record_usage(self, request, handler):
                """模拟伴随工具执行的业务副作用。"""
                return await handler(request)

            report_plugins.on_tool_call = record_usage
        error = rf"{table} id={row_id}.*reports.*report-plugin.*升级后"
        with pytest.raises(ValueError, match=error):
            await apply_plugin_migration(manager, review)
        with pytest.raises(ValueError, match=error):
            await export_plugin_migration(manager, tmp_path / "expanded", legacy_builtins=confirmed)
        async with engine.begin() as conn:
            await assert_legacy_schema(conn)
            read_config = text(f"SELECT {column} FROM {table} WHERE id=:id")
            write_config = text(f"UPDATE {table} SET {column}=CAST(:config AS JSON) WHERE id=:id")
            assert await conn.scalar(read_config, {"id": row_id}) == original
            context = original["context"] if table == "agents" else original
            invalid_selection = {"report-plugin" if malformed_field == "plugins" else "reports": False}
            malformed = {**context, malformed_field: invalid_selection}
            persisted = {"context": malformed} if table == "agents" else malformed
            await conn.execute(write_config, {"config": json.dumps(persisted), "id": row_id})
        with pytest.raises(ValueError, match=rf"{table} id={row_id} 的 {malformed_field} 无法解析.*列表"):
            await export_plugin_migration(manager, tmp_path / "malformed", legacy_builtins=confirmed)
        async with engine.begin() as conn:
            assert await conn.scalar(read_config, {"id": row_id}) == persisted
            context["plugins"] = [" report-plugin ", "report-plugin "]
            await conn.execute(write_config, {"config": json.dumps(original), "id": row_id})
        selected = tmp_path / "explicit"
        await export_plugin_migration(manager, selected, legacy_builtins=confirmed)
        await apply_plugin_migration(manager, selected)
        async with engine.connect() as conn:
            after = await conn.scalar(read_config, {"id": row_id})
            context = after["context"] if table == "agents" else after
            assert context == {"mcps": [] if table == "agents" else "all", "plugins": ["report-plugin"]}
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_plugin_migration_cli_registers_models_in_a_cold_process(tmp_path):
    """验证冷进程的插件迁移及正式 v0.8.0 到当前版本的两步升级。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_cli")
    script = textwrap.dedent('''
        import os
        import runpy
        import sys
        from sqlalchemy.ext.asyncio import create_async_engine
        from yuxi.infrastructure.postgres.manager import PostgresManager
        from yuxi.infrastructure.postgres import models

        def initialize_isolated(self):
            """仅替换连接边界，将正式 CLI 的全部查询限制在测试 Schema。"""
            self.async_engine = create_async_engine(
                os.environ["POSTGRES_URL"],
                connect_args={"server_settings": {"search_path": os.environ["PLUGIN_CLI_TEST_SCHEMA"]}},
            )
            self._initialized = True

        PostgresManager.initialize = initialize_isolated
        if os.environ.get("PLUGIN_CLI_OMIT_MODELS") == "1":
            models.load_models = lambda: None
        runpy.run_module(sys.argv.pop(1), run_name="__main__")
    ''')
    review = tmp_path / "review"
    isolated_env = {
        **os.environ,
        "PLUGIN_CLI_TEST_SCHEMA": schema,
        "YUXI_SKILL_DATA_DIR": str(tmp_path / "skills"),
    }
    try:
        await create_legacy_schema(manager)

        for action, omit_models in (("export", "1"), ("export", "0"), ("apply", "0")):
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script, "yuxi.migrations.agent_plugins", action, str(review)],
                env={**isolated_env, "PLUGIN_CLI_OMIT_MODELS": omit_models},
                capture_output=True,
                text=True,
                timeout=30,
            )
            if omit_models == "1":
                assert result.returncode != 0
                assert "InvalidRequestError" in result.stderr and "AgentEnv" in result.stderr, result.stderr
                assert not review.exists()
            else:
                assert result.returncode == 0, result.stderr
                assert json.loads((review / "plan.json").read_text()) == {
                    "source_version": 2,
                    "confirmed_legacy_builtins": [],
                    "legacy_builtin_mcps": {},
                    "skills": [],
                    "configs": [],
                }
            async with engine.connect() as conn:
                assert dict((await conn.execute(text("SELECT domain,version FROM yuxi_schema_migrations"))).all()) == {
                    "business": 5 if action == "apply" else 2,
                    "knowledge": 2,
                }
        async with engine.connect() as conn:
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() "
                        "AND table_name='skills' AND column_name IN ('tool_dependencies','mcp_dependencies','skill_dependencies')"
                    )
                )
                == 0
            )
            column = await conn.execute(
                text("""SELECT data_type, is_nullable, column_default,
                       col_description('mcp_servers'::regclass, ordinal_position::int)
                       FROM information_schema.columns
                       WHERE table_schema=current_schema() AND table_name='mcp_servers' AND column_name='code_owned'""")
            )
            assert column.one() == ("boolean", "NO", "false", "由代码发布的内置连接")
            projection_type = text(
                "SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() "
                "AND table_name='sessions' AND column_name='agent_state_view'"
            )
            assert await conn.scalar(projection_type) is None

        with pytest.raises(RuntimeError, match="business=5"):
            await require_current_schema(manager)
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script, "yuxi.migrations.agent_state_view"],
            env=isolated_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        await require_current_schema(manager)
        assert await get_schema_versions(manager) == {"business": 6, "knowledge": 2}
        async with engine.connect() as conn:
            assert await conn.scalar(projection_type) == "jsonb"
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("template_config", [{}, {"system_prompt": "preserved"}])
async def test_plugin_upgrade_requires_reviewed_deployment_and_preserves_content(tmp_path, monkeypatch, template_config):
    """缺少部署声明拒绝；部署后转换内容与选择，重复升级不改变事实。"""
    from yuxi.migrations import agent_plugins
    from yuxi.modules.extensions.plugins import registry

    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_upgrade")
    root = tmp_path / "skills"
    source = root / "shared" / "custom-research"
    source.mkdir(parents=True)
    original = (
        "---\nname: custom-research\ndescription:\n  Research.\n"
        '  CREATE (from scratch): "report".\ntool_dependencies: [web_search]\nmcp_dependencies: [reports]\n---\n'
    ).replace("\n", "\r") + "Research body\r\n"
    (source / "SKILL.md").write_text(original)
    monkeypatch.setattr(agent_plugins, "get_skill_data_dir", lambda: root)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / "personal-workspace")
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    monkeypatch.setattr(registry, "MIGRATED_PLUGIN_DIRECTORY", deployment)
    registry.get_plugins.cache_clear()
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('owner','owner','unused','user',0,0)"
                )
            )
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default,created_by)
                VALUES ('demo','chatbot','Demo','[]',
                '{"context":{"skills":[" custom-research ","custom-research "],
                "preload_skills":[" custom-research "],"tools":["web_search"]}}',
                '{}','private',false,false,'owner')""")
            )
            await conn.execute(
                text("""INSERT INTO skills(slug,name,description,source_type,dir_path,share_config,enabled,created_by,
                tool_dependencies,mcp_dependencies)
                VALUES ('custom-research','Custom research','Research. CREATE (from scratch): "report".','upload','shared/custom-research',
                '{"version": 2,"read_scope":{"access_level":"global","department_ids":[],"user_uids":[]},
                "manage_scope": null}',true,'owner','["web_search"]','["reports"]')""")
            )
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('shared-template','chatbot','Shared','[]',CAST(:config AS JSON),'{}','shared',true,false)"""),
                {"config": json.dumps(template_config)},
            )
        review = tmp_path / "review"
        async with engine.begin() as conn:
            await conn.execute(text("""UPDATE skills SET mcp_dependencies='["knowledge"]' WHERE slug='custom-research'"""))
        (source / "SKILL.md").write_text(original.replace("[reports]", "[knowledge]"))
        with pytest.raises(ValueError, match="custom-research.*专属 MCP.*knowledge"):
            await export_plugin_migration(manager, review)
        assert not review.exists()
        async with engine.begin() as conn:
            await conn.execute(text("""UPDATE skills SET mcp_dependencies='["reports"]' WHERE slug='custom-research'"""))
        (source / "SKILL.md").write_text(original)
        personal = tmp_path / "personal-workspace" / "agents" / "skills" / "old-personal" / "SKILL.md"
        personal.parent.mkdir(parents=True)
        personal.write_text("---\nname: old-personal\ndescription: old\nmcp_dependencies: []\n---\n")
        with pytest.raises(ValueError, match="个人 Skill 的旧依赖从未生效"):
            await export_plugin_migration(manager, review)
        legacy_personal = personal.read_text()
        personal.unlink()
        plan = await export_plugin_migration(manager, review)
        personal.write_text(legacy_personal)
        with pytest.raises(ValueError, match="个人 Skill 的旧依赖从未生效"):
            await apply_plugin_migration(manager, review)
        assert personal.read_text() == legacy_personal
        personal.unlink()
        assert plan["configs"][1]["after"] == {**template_config, "context": {"plugins": ["custom-research"]}}
        expected_context = {"skills": [], "preload_skills": [], "tools": ["web_search"], "plugins": ["custom-research"]}
        assert plan["configs"][0]["after"]["context"] == expected_context
        with pytest.raises(ValueError, match="未知插件"):
            await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            await assert_legacy_schema(conn)
        declaration = (review / "deployed" / "custom-research.json").read_text()
        incompatible = json.loads(declaration)
        incompatible["mcps"] = []
        (deployment / "custom-research.json").write_text(json.dumps(incompatible))
        with pytest.raises(ValueError, match="已部署插件与审阅声明不匹配"):
            await apply_plugin_migration(manager, review)
        (deployment / "custom-research.json").write_text(declaration)
        (source / "SKILL.md").write_text(original + "Changed after review\n")
        with pytest.raises(ValueError, match="源数据或内容已变化"):
            await apply_plugin_migration(manager, review)
        (source / "SKILL.md").write_text(original)
        await apply_plugin_migration(manager, review)
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'")) == 5
            config = await conn.scalar(text("SELECT config_json FROM agents WHERE slug='demo'"))
            assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='shared-template'")) == {
                **template_config,
                "context": {"plugins": ["custom-research"]},
            }
            config = config["context"]
            assert config == expected_context
            current = await conn.scalar(text("SELECT dir_path FROM skills WHERE slug='custom-research'"))
            assert "tool_dependencies" not in (root / current / "SKILL.md").read_text()
            assert (root / current / "SKILL.md").read_bytes().endswith(b"Research body\r\n")
            from yuxi.modules.extensions.skills.package import parse_skill_markdown

            assert parse_skill_markdown((root / current / "SKILL.md").read_text())[2] == 'Research. CREATE (from scratch): "report".'
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() "
                        "AND table_name='skills' AND column_name='tool_dependencies'"
                    )
                )
                == 0
            )
        assert (source / "SKILL.md").read_bytes() == original.encode("utf-8")
        assert "tool_names" not in json.loads(declaration)
        assert plan["skills"][0]["tool_dependencies"] == ["web_search"]
    finally:
        registry.get_plugins.cache_clear()
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("table,status", [("agent_runs", "running"), ("agent_inputs", "pending"), ("agent_turns", "waiting")])
async def test_plugin_upgrade_rejects_resumable_work_before_any_mutation(tmp_path, table, status):
    """待消费输入或可恢复 Turn 即使没有活动执行也不能升级。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_pending")
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await create_legacy_project(conn, "owner")
            await conn.execute(
                text("""INSERT INTO sessions(thread_id,tree_root_thread_id,uid,agent_id,project_id,is_pinned,config_snapshot)
                VALUES ('thread','thread','owner','agent','project',false,'{}')""")
            )
            await conn.execute(
                text("""INSERT INTO agent_turns(id,thread_id,uid,status,created_at)
                VALUES ('turn','thread','owner',:status,CURRENT_TIMESTAMP)"""),
                {"status": status if table == "agent_turns" else "completed"},
            )
            await conn.execute(
                text("""INSERT INTO agent_runs(id,execution_seq,thread_id,runtime_scope_id,agent_slug,uid,status,turn_id,source,channel,
                run_type,origin_metadata,input_payload,manifest,token_usage,runtime_cleanup_pending)
                VALUES ('run',1,'thread','thread','agent','owner',:status,'turn','chat','web','chat','{}','{}','{}','{}',false)"""),
                {"status": status if table == "agent_runs" else "interrupted"},
            )
            await conn.execute(
                text("""INSERT INTO agent_inputs(id,thread_id,uid,agent_slug,kind,status,messages,attachment_file_ids,
                input_payload,source,channel,origin_metadata,created_at,cancelled_at)
                VALUES ('input','thread','owner','agent','follow_up',:status,'[]','[]','{}','chat','web','{}',CURRENT_TIMESTAMP,
                CASE WHEN CAST(:status AS VARCHAR)='cancelled' THEN CURRENT_TIMESTAMP ELSE NULL END)"""),
                {"status": status if table == "agent_inputs" else "cancelled"},
            )
        with pytest.raises(ValueError, match="排空"):
            await export_plugin_migration(manager, tmp_path / "review")
        async with engine.connect() as conn:
            assert await conn.scalar(text(f"SELECT status FROM {table}")) == status
        assert not (tmp_path / "review").exists()
        async with engine.begin() as conn:
            terminal = "cancelled" if table == "agent_inputs" else "completed"
            cancelled_at = ",cancelled_at=CURRENT_TIMESTAMP" if table == "agent_inputs" else ""
            await conn.execute(text(f"UPDATE {table} SET status=:status{cancelled_at}"), {"status": terminal})
        plan = await export_plugin_migration(manager, tmp_path / "review")
        assert plan["configs"] == [] and plan["skills"] == []
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("enabled", [False, True])
async def test_migration_removes_disabled_or_revoked_entries_from_session(tmp_path, monkeypatch, enabled):
    """旧入口必须退出普通选择；被停用或撤权时不转换为插件授权。"""
    from yuxi.migrations import agent_plugins

    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_revoked")
    root = tmp_path / "skills"
    source = root / "shared" / "restricted"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: restricted\ndescription: Test\ntool_dependencies: [web_search]\nmcp_dependencies: [reports]\n---\nBody\n"
    )
    monkeypatch.setattr(agent_plugins, "get_skill_data_dir", lambda: root)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / uid)
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await create_legacy_project(conn, "reader")
            await conn.execute(
                text("""INSERT INTO sessions(thread_id,tree_root_thread_id,cooperation_name,cooperation_path,
                uid,agent_id,project_id,is_pinned,config_snapshot)
                VALUES ('thread','thread','root','/root','reader','demo','project',false,
                '{"skills":[" restricted ","restricted "],"preload_skills":[" restricted "],"tools":["web_search"]}')""")
            )
            await conn.execute(
                text("""INSERT INTO skills(slug,name,description,source_type,dir_path,share_config,enabled,created_by,
                tool_dependencies,mcp_dependencies)
                VALUES ('restricted','Restricted','Test','upload','shared/restricted',
                CAST(:grants AS JSON),:enabled,'another-owner','["web_search"]','["reports"]')"""),
                {
                    "enabled": enabled,
                    "grants": json.dumps(
                        {"version": 2, "read_scope": None if enabled else {"access_level": "global"}, "manage_scope": None}
                    ),
                },
            )
        plan = await export_plugin_migration(manager, tmp_path / "review")
        change = next(change for change in plan["configs"] if change["kind"] == "session")
        assert change["after"] == {"skills": [], "preload_skills": [], "tools": ["web_search"]}
        async with engine.connect() as conn:
            original = await conn.scalar(text("SELECT config_snapshot FROM sessions"))
            assert original["skills"] == [" restricted ", "restricted "]
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize(
    ("tools", "failure"),
    [(None, None), ("all", None), ([], None), (["execute"], None), ([], "copy"), ([], "commit_response")],
)
@pytest.mark.parametrize("slug", ["deep-research", "image-gen"])
async def test_content_builtin_preserves_skill_and_tool_selection(tmp_path, monkeypatch, tools, failure, slug):
    """数据库迁移清除旧依赖，保留图像和研究 Skill 和工具选择。"""
    from yuxi.migrations import agent_plugins

    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_plain_skill")
    root = tmp_path / "skills"
    source = root / "shared" / slug
    source.mkdir(parents=True)
    dependencies = ["chat-widgets"] if slug == "image-gen" else []
    original = (
        f"---\nname: {slug}\ndescription: Research\ntool_dependencies: [web_search]\n"
        f"skill_dependencies: {json.dumps(dependencies)}\n---\nResearch body\n"
    )
    (source / "SKILL.md").write_text(original)
    monkeypatch.setattr(agent_plugins, "get_skill_data_dir", lambda: root)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / uid)
    context = {"skills": [slug], "preload_skills": [slug]}
    if tools is not None:
        context["tools"] = tools
    expected = dict(context)
    try:
        await create_legacy_schema(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('research','chatbot','Research','[]',CAST(:config AS JSON),'{}','shared',true,false)"""),
                {"config": json.dumps({"context": context})},
            )
            await conn.execute(
                text("""INSERT INTO skills(slug,name,description,source_type,dir_path,share_config,enabled,
                tool_dependencies,skill_dependencies)
                VALUES (:slug,'Research','Research','builtin',:path,'{}',true,'["web_search"]',CAST(:dependencies AS JSON))"""),
                {"slug": slug, "path": f"shared/{slug}", "dependencies": json.dumps(dependencies)},
            )
            if dependencies:
                helper = root / "shared" / "chat-widgets"
                helper.mkdir()
                (helper / "SKILL.md").write_text("---\nname: chat-widgets\ndescription: Widgets\n---\nWidget body\n")
                await conn.execute(
                    text("""INSERT INTO skills(slug,name,description,source_type,dir_path,share_config,enabled)
                    VALUES ('chat-widgets','Widgets','Widgets','builtin','shared/chat-widgets','{}',true)""")
                )
        review = tmp_path / "review"
        plan = await export_plugin_migration(manager, review)
        assert plan["skills"][0]["plugin"] is None
        assert plan["skills"][0]["tool_dependencies"] == ["web_search"]
        assert list((review / "deployed").iterdir()) == []
        assert plan["configs"] == []
        if failure:
            with monkeypatch.context() as fault:
                if failure == "copy":
                    original_copy = agent_plugins.copy_skill_tree_no_symlinks

                    def copy_then_fail(old, target):
                        """在提交前制造确定失败，验证临时包回收。"""
                        original_copy(old, target)
                        raise OSError("injected copy failure")

                    fault.setattr(agent_plugins, "copy_skill_tree_no_symlinks", copy_then_fail)
                else:
                    original_begin = type(engine).begin

                    @asynccontextmanager
                    async def commit_then_fail(self):
                        """真实提交事务后丢失客户端响应，模拟提交结果未知。"""
                        async with original_begin(self) as conn:
                            yield conn
                        raise OSError("injected commit response failure")

                    fault.setattr(type(engine), "begin", commit_then_fail)
                with pytest.raises(OSError, match="injected"):
                    await apply_plugin_migration(manager, review)
            async with engine.connect() as conn:
                version = await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'"))
                referenced = await conn.scalar(text("SELECT dir_path FROM skills WHERE slug=:slug"), {"slug": slug})
            if failure == "copy":
                assert version == 2
                assert referenced == f"shared/{slug}"
                assert list((root / "packages" / slug).iterdir()) == []
            else:
                assert version == 5
                assert referenced != f"shared/{slug}"
                assert (root / referenced / "SKILL.md").read_text().endswith("Research body\n")
                assert "tool_dependencies" not in (root / referenced / "SKILL.md").read_text()
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='research'")) == {"context": expected}
            current = await conn.scalar(text("SELECT dir_path FROM skills WHERE slug=:slug"), {"slug": slug})
            assert "tool_dependencies" not in (root / current / "SKILL.md").read_text()
            assert "Research body" in (root / current / "SKILL.md").read_text()
        assert (source / "SKILL.md").read_text() == original
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def create_legacy_project(conn, uid):
    """建立旧会话引用的用户与项目。"""
    await conn.execute(
        text("""INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted)
        VALUES (:uid,:uid,'unused','user',0,0)"""),
        {"uid": uid},
    )
    await conn.execute(
        text("""INSERT INTO projects(id,uid,selection_status,workdir_path,directory_mode)
        VALUES ('project',:uid,'implicit','work','managed')"""),
        {"uid": uid},
    )
