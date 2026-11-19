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
from yuxi.migrations.agent_plugins import apply_plugin_migration, export_plugin_migration
from yuxi.migrations.schema import create_business_tables, create_schema_version_table, record_schema_version

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(
    "mcps,expected",
    [
        (["abridge", "reports"], {"mcps": ["reports"], "plugins": ["abridge"]}),
        ("all", {"mcps": "all", "plugins": ["abridge"]}),
        ([], {"mcps": []}),
    ],
)
async def test_first_upgrade_converts_device_selection_once(tmp_path, monkeypatch, mcps, expected):
    """旧生产配置一次升级到当前版本，重跑不恢复已关闭的设备插件。"""
    from yuxi.migrations import agent_plugins

    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_first_upgrade")
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / uid)
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        async with engine.begin() as conn:
            for field in agent_plugins.DEPENDENCY_FIELDS:
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default)
                VALUES ('device','chatbot','Device','[]',CAST(:config AS JSON),'{}','shared',false,false)"""),
                {"config": json.dumps({"system_prompt": "kept", "context": {"mcps": mcps}})},
            )
        review = tmp_path / "review"
        await export_plugin_migration(manager, review)
        await apply_plugin_migration(manager, review)
        async with engine.begin() as conn:
            assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='device'")) == {
                "system_prompt": "kept",
                "context": expected,
            }
            assert await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'")) == 5
            await conn.execute(
                text("UPDATE agents SET config_json=CAST(:config AS JSON) WHERE slug='device'"),
                {"config": json.dumps({"context": {"mcps": [], "plugins": []}})},
            )
        await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='device'")) == {"context": {"mcps": [], "plugins": []}}
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_plugin_migration_cli_registers_models_in_a_cold_process(tmp_path):
    """正式模块入口独立装配 ORM，负控复现 pytest 预加载曾掩盖的缺陷。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_plugin_cli")
    script = textwrap.dedent('''
        import os
        import runpy
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
        if os.environ["PLUGIN_CLI_OMIT_MODELS"] == "1":
            models.load_models = lambda: None
        runpy.run_module("yuxi.migrations.agent_plugins", run_name="__main__")
    ''')
    review = tmp_path / "review"
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        async with engine.begin() as conn:
            for field in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))

        for action, omit_models in (("export", "1"), ("export", "0"), ("apply", "0")):
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script, action, str(review)],
                env={
                    **os.environ,
                    "PLUGIN_CLI_TEST_SCHEMA": schema,
                    "PLUGIN_CLI_OMIT_MODELS": omit_models,
                    "YUXI_SKILL_DATA_DIR": str(tmp_path / "skills"),
                },
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
                assert json.loads((review / "plan.json").read_text()) == {"version": 1, "skills": [], "configs": []}
            async with engine.connect() as conn:
                assert await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'")) == (
                    5 if action == "apply" else 2
                )
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
        '  CREATE (from scratch): "report".\ntool_dependencies: [web_search]\nmcp_dependencies: [reports]\n---\nResearch body\n'
    )
    (source / "SKILL.md").write_text(original)
    monkeypatch.setattr(agent_plugins, "get_skill_data_dir", lambda: root)
    monkeypatch.setattr(agent_plugins, "user_workspace_dir", lambda uid: tmp_path / "personal-workspace")
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    monkeypatch.setattr(registry, "MIGRATED_PLUGIN_DIRECTORY", deployment)
    registry.get_plugins.cache_clear()
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        async with engine.begin() as conn:
            for field in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))
            await conn.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('owner','owner','unused','user',0,0)"
                )
            )
            await conn.execute(
                text("""INSERT INTO agents(slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default,created_by)
                VALUES ('demo','chatbot','Demo','[]',
                '{"context":{"skills":["custom-research"],"tools":["web_search"]}}', '{}','private',false,false,'owner')""")
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
        assert plan["configs"][0]["after"]["context"] == {"skills": [], "tools": ["web_search"], "plugins": ["custom-research"]}
        with pytest.raises(ValueError, match="未知插件"):
            await apply_plugin_migration(manager, review)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'")) == 2
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
            assert config["plugins"] == ["custom-research"] and config["tools"] == ["web_search"]
            current = await conn.scalar(text("SELECT dir_path FROM skills WHERE slug='custom-research'"))
            assert "tool_dependencies" not in (root / current / "SKILL.md").read_text()
            assert "Research body" in (root / current / "SKILL.md").read_text()
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
        assert (source / "SKILL.md").read_text() == original
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
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        async with engine.begin() as conn:
            for field in ("tool_dependencies", "mcp_dependencies", "skill_dependencies"):
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))
            await conn.execute(
                text("""INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted)
                VALUES ('owner','owner','unused','user',0,0)""")
            )
            await conn.execute(
                text("""INSERT INTO projects(id,uid,selection_status,workdir_path,directory_mode)
                VALUES ('project','owner','implicit','work','managed')""")
            )
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
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        async with engine.begin() as conn:
            for field in agent_plugins.DEPENDENCY_FIELDS:
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))
            await conn.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('reader','reader','unused','user',0,0)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO projects(id,uid,selection_status,workdir_path,directory_mode) "
                    "VALUES ('project','reader','implicit','work','managed')"
                )
            )
            await conn.execute(
                text("""INSERT INTO sessions(thread_id,tree_root_thread_id,cooperation_name,cooperation_path,
                uid,agent_id,project_id,is_pinned,config_snapshot)
                VALUES ('thread','thread','root','/root','reader','demo','project',false,
                '{"skills":["restricted"],"preload_skills":["restricted"],"tools":["web_search"]}')""")
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
            assert original["skills"] == ["restricted"]
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
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        async with engine.begin() as conn:
            for field in agent_plugins.DEPENDENCY_FIELDS:
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {field} JSON NOT NULL DEFAULT '[]'"))
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
