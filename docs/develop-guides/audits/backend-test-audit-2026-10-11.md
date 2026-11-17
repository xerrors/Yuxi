# 后端测试资产审计报告（backend/test/）

面向 Yuxi 维护者，回答一个问题：`backend/test/` 下的测试资产里，哪些是开发期遗留的重复与低质量测试、哪些可以合并、按什么类型分类处置。本记录是审计调研。它不是执行决定。全部结论来自逐文件精读，且经过了完整的复核轮（第十一节）与两轮启发式发现（第十二节）。关键结论均经人工抽查源码复核。执行任何合并/删除前应按 [测试规范](../testing-guidelines.md) 补齐对应证据。

调研日期：2026-10-11（v2 修订同日）。范围：`backend/test/` 全部测试文件——主审快照约 336 个 `test_*.py`，复核时点实测 339 个（审计期间工作树并行演进：staged 新增 `test_agent_feedback` 三件套与 `test_debug_message_projection.py`，四个文件均已补审，见 11.3 快照漂移说明），总量约 8.9 万行、约 2400 个测试函数，外加根目录 `conftest.py`、`live_api_cleanup.py` 与 `support/` 共享模块。

## 一、总体结论

**先纠正一个预期**：这批测试不是"开发期一次性堆积、大量互相重复"的典型形态。逐文件核查后，真正指向已删除代码或已完成使命的"开发期遗留"只有约 10 处；绝大多数断言字段级精确、负向案例齐全、清理路径完整，unit/integration/e2e 三层在多数主题上是刻意分层。三层不是重复。与 [工程信任系统](../engineering-trust.md) 的证据规则对照，这套资产的整体质量高于常见历史积累。

**真实的债务在组织形态，不在测试质量**。主要表现为七类：

1. **场景级真重复**（同一契约在两个文件各断言一次，删一个不损覆盖）：约 14 处，集中在 integration/api 的 public/knowledge/key 边界、integration/services 的 run lease 与约束负向、unit 的 message recorder 与 state view。
2. **文件切分过细与巨石并存**：约 20 个 30-90 行的单测试文件，与 6 个 1000-2100 行的巨石文件同时存在；后者大量近似场景未参数化。
3. **脚手架复制**：同一 fixture/helper 在多个文件逐字复制（integration 目录 55 处 no-op 清理 fixture 空定义、`test_skill_service.py` 内联 21 个 FakeRepo、sandbox 两个巨型文件各复制 stub 数十次），估算收敛后可在不丢任何断言的前提下减少 2000-3000 行。
4. **命名与归位失焦**：约 15 个文件名与内容脱节（如 `test_file_listing_scaling.py` 全文没有规模断言、`test_parser_facade.py` 并非 facade、两个 `RunMessageRecorder` 测试文件仍用旧模块别名命名），另有 8 个测试散落在 `unit/` 根目录。
5. **少量死代码与脆弱 oracle**：恒真的 `assert not hasattr(...)`、永不触发的 skip、写死依赖版本号、mock 掉被测函数后断言 fake 自身返回值（`test_mention_router.py`、`test_skill_router.py` 是最差两例）。
6. **覆盖与接线缺口**（复核轮新增）：CI 对全部环境变量门控测试零引用，BUILTIN_PROVIDERS 目录事实与沙箱 grep 镜像契约处于"默认不跑 + CI 不跑"的双零覆盖；4 个 integration 文件不在 CI 白名单、2 个文件缺 `integration` 筛选标记（`test_provider_reasoning_live.py` 零标记、`test_sandbox_native_grep.py` 仅 asyncio，已实测；另有 5 个文件缺文件级 pytestmark，靠逐测试标记等效覆盖）；e2e 层 9 个文件缺 timeout 标记且同文件混用 `api:` 与 `localhost:` 两种 replay 地址。
7. **patch 依赖 re-export 别名链**（复核轮新增）：`ModelCache` 单例经 5 条 re-export 别名被 20+ 处 patch，skills 域大量 patch 打在 `__init__` 别名。这些 patch 不是打在定义模块——一次无害的 import 整理会同时打断多个测试文件。

处置主线是"合并、归位、参数化、收敛 fixture、补齐接线"。它不是大面积删测试。可直接删除的只有第五类中的死代码与个别纯墓碑测试。

## 二、测试债务类型分类

以下分类覆盖全部发现的问题类型，每类给出判据、规模与处理原则。逐文件归属见第十节索引表。

### A. 场景重复型（真重复，可删/二选一）

判据：两个测试构造相同的前置事实、调用同一被测入口、断言同一结果，且任一方没有另一方不具备的增量。

处理原则：保留断言更强或层次更合适的一份，删除另一份；若两侧各有一小段独有断言，合并为参数化。

典型（完整清单见第四节）：

- `integration/api/test_public_knowledge_key_boundary.py::test_knowledge_key_is_limited_to_public_knowledge_api` 与 `test_public_knowledge_tools.py::test_knowledge_key_tool_route_boundary_without_kb`：都是"创建 knowledge key → `list_kbs` 200 → `/api/v1/agents` 403 → 清理"。**全库最强的一处真重复**。复核确认合并时不可丢失两侧独有断言：boundary 侧 5 条管理路径 403 清单与 external 墓碑，tools 侧 `download_kb_file` 404 断言。
- `integration/services/test_agent_run_lease.py` 与 `test_agent_run_manifest_and_attempts.py`：lease owner fencing 与 `lease_expired` attempt 事实在两文件各断言一次——复核实测重复程度比初稿更重。`match="lease owner"` 共 7 处断言（lease 侧 91/236/323/435/462，manifest 侧 294/306/355），全部收敛到同一生产 guard `yuxi/modules/agents/repositories/runs.py:965-967 _require_lease_owner`。
- `unit/services/test_chat_service_sync.py` 的 3 个 `get_agent_state_view` 测试（实际 def 行 1077/1104/1180）与 `test_checkpoint_state_reader.py` 的 2 个：同被测函数、同 mock 手法，跨文件拆裂。

### B. 开发期遗留型（死代码、过期守卫、脆弱 oracle）

判据：守护的符号/参数/路由已从源码删除（grep 证实零命中）；或断言恒真；或依赖外部已消失的状态。

处理原则：直接删除；若删除会失去唯一覆盖，先确认被测行为是否仍需要守护。

典型：

- `unit/repositories/test_session_workdir.py`：2 个测试显式守护已从 `add_session` 移除的 `workdir_path` 参数（L35 `assert not hasattr(...)`、L39 `pytest.raises(TypeError)`；`yuxi/modules/agents/repositories/sessions.py` 中 grep 零命中，已复核），另有 2 个迁移期副产品（L49-55 project_id 必填契约、L58-67 flush 失败传播为活契约）。
- `unit/knowledge/` 三处恒真断言：`test_knowledge_base_update.py:221`、`test_knowledge_runtime_cache.py:96` 的 `assert not hasattr(..., "_runtime_configs")`，`test_lazy_file_metadata.py:109` 的 `files_meta` 同理（两符号在 `yuxi/` 零命中，已复核）。
- `unit/services/test_chat_service_sync.py:48` 的 `_fake_normalize_agent_context_config`、`test_chat_stream_interrupt.py:14-25` 的 `_FakeSession`/`_resolve_test_workdir`：定义后全文件无引用（已复核；`_resolve_test_workdir` 确从 sync 文件复制而来，sync 侧在 L1304 有使用，本文件没有）。另有复核轮补查出的 5 处死 helper（见第十二节 N9）。
- `integration/services/test_knowledge_stats_refresh.py::test_retired_stats_repair_route_cannot_mutate_persisted_facts`：为已删除的 `stats/repair` 路由专门起真实 uvicorn 断言 404，成本高收益低。
- `e2e/test_milvus_text_retrieval_e2e.py:206-207` 写死 `pymilvus.__version__ == "3.0.2"`，升级即崩。
- `unit/knowledge/test_parser_facade.py:217`：skip 条件 `.endswith(".xls")` 在 `.docx/.pptx/.xlsx` 参数下永不触发（已复核 parametrize 列表）。
- `test/live_api_cleanup.py` 的 legacy 标题模式与 5 个孤儿 marker（agent-sync-e2e/agent-steer-e2e/attachment-state-e2e/subagent-stream-e2e/viewer-fs-e2e，grep e2e/ 全目录零命中；当前 e2e 实际只生成 agent-async-e2e、ocr-config-e2e、personal-skill-e2e、read-file-e2e 四种；`YUXI_SUBAGENT_STREAM_E2E_` 前缀 L195 同为孤儿）。有注释与单测，属有意债务，应设退役期限。

### C. 微文件型（文件粒度过细）

判据：30-90 行、1-2 个测试、无自有复杂 fixture；或与另一文件同一被测对象。

处理原则：并入同被测对象/同 fixture 域的文件；被测对象本身独立成模块且契约不可归并的（如 `test_package_import.py` 的包级懒加载契约）可保留。

涉及约 20 个文件，合并清单见第五节。

### D. 巨石文件型（内部未参数化的近似场景堆积）

判据：单文件 800-2100 行；同一模板的测试反复手写；或一个文件混杂 3-4 个互不相关的被测对象。

处理原则：先提共享 fixture 工厂，再把同模板场景参数化；混杂主题按被测对象拆分。注意"参数化合并"目标是减少样板。目标不是减少断言。

典型（数量均经复核 grep 精确验证）：`unit/services/skills/test_skill_service.py`（2103 行/63 测，21 处内联 FakeRepo、17 次 `setattr(svc, "SkillRepository", ...)`、zip 导入族 5 测 + md 变体共 6 测同模板、仅 7 处 parametrize）、`unit/sandbox/test_sandbox_backends.py`（1829 行/77 测，同一 stub 49 处、错误串 5+2 处）、`unit/sandbox/test_sandbox_provisioner_config.py`（1824 行/61 测，`_load_module()` 56 处、`PROVISIONER_BACKEND` setenv 38 处、k8s stub 块 4 份）、`integration/services/test_session_cooperation.py`（1500 行/39 测）、`unit/services/test_chat_service_sync.py`（1313 行、四主题杂烩、save_messages 族 10 测各手写 52-89 行样板）、`unit/graphs/test_milvus_graph_build.py`（1375 行，kb 配置 SimpleNamespace 7 处同模板、ChunkRepo 假类 5 份）。

### E. 脚手架复制型（跨文件 fixture/helper 重复）

判据：同一 helper/fixture/常量表在多个文件逐字出现。

处理原则：上提到对应层级 conftest 或 `test/support/`；不改变任何测试语义。

典型（完整清单见第七节，数量均经复核更正）：integration 目录 55 处 no-op 清理 fixture 空定义（16/19/20）；21 个文件手写 sqlite 内存引擎而 `test/support/sqlite.py` 已存在；e2e 三个文件各自复制附件清理样板；`test_permission_revocation_e2e.py` 自带约 124 行 replay Handler 与 `support/openai_replay_server.py` 平行；`_create_thread` 在 7 个文件定义（integration 侧 4 份 + e2e 侧 3 份）。

### F. 命名失焦型

判据：文件名指向的模块/主题与实际被测对象不符。

处理原则：更名。名称是测试导航的主要索引，失焦的代价是重复建测试和找错文件。

典型：`test_model_message_audit_service.py`/`test_tool_message_audit_service.py`（旧模块别名，测的都是 `message_recorder`）、`test_file_listing_scaling.py`（10 测无任何 scaling 断言，grep 零命中，已复核）、`test_knowledge_manager_ownership.py`（ownership/effective_permission 零命中）、`test_parser_facade.py`（解析域杂烩）、`test_run_worker.py`（1749 行/54 测覆盖 4 个被测对象，且 `import ... as run_worker` 把旧模块名硬编码进别名——源码无 run_worker 模块）、`test_chat_service_sync.py`（四主题杂烩）、`test_openai_events_e2e.py`（测默认模式合批与取消，非事件协议）、`test_mcp_stdio_security.py`（e2e 目录唯一无 `_e2e` 后缀，且仅有裸 `pytest.mark.e2e` 标记）、`unit/utils/test_paths.py`（测 `infrastructure/filesystem`，与 `unit/workspace/test_filesystem.py` 撞名）。

### G. mock 测 mock / oracle 无信息量

判据：mock 掉被测函数本身或其核心依赖后，断言的是 fake 自己返回的数据；或断言弱到任何实现都能通过。

处理原则：重写为测异常映射/副作用等真实契约；无独占价值则删除。

典型（复核轮确认了两例，后者更严重）：

- `unit/routers/test_mention_router.py`（64 行）：被测端点含参数转发 + 异常映射（`mentions.py:42-45`），两个测试 mock 掉 `search_mentions` 后断言 fake 硬编码路径，**恰好漏测了异常映射**，建议重写或删除。
- `unit/routers/test_skill_router.py`（408 行/14 测）：**12/14 个测试 patch 掉服务/repository 权限闸门后仅断言参数转发**；其中 `test_dependency_options_route_checks_manage_permission`（def L271）与 `test_skill_export_route_still_checks_manage_permission`（def L320）名字声称测权限、patch 的恰是权限闸门 `get_manageable_skill_or_raise` 本身，且 `_build_app`（def L12 起）本地重写 role→403 规则，真实 `get_admin_user` 语义变化时假实现静默漂移——直接违反根 [AGENTS.md](https://github.com/xerrors/Yuxi/blob/main/AGENTS.md)"权限在后端依赖与 repository 可见性查询处最终执行"的条款，比 mention_router 更具误导性。文件内 `test_builtin_routes_require_admin`（def L386）是唯一正例。
- 弱断言案例：`integration/api/test_graph_build_status.py`（25 行只断言空库零态）；`unit/services/test_agent_transport.py:160` 与 `unit/services/test_run_worker.py:1171` 两个"不抛异常即通过"的 best-effort 契约（语义可辩护，应加注释说明）。

### H. 跨层复述型（多为刻意分层，清理需谨慎）

判据：unit/integration/e2e 三层覆盖同一能力链。

处理原则：**默认保留**。本库的分层纪律良好（`test/support/sqlite.py` docstring 明示"SQLite 证逻辑、Postgres 证并发与约束"）。只有两层断言完全同构、且上层增量只是把 mock 换成真实现，才裁剪较弱一层；其余以文件头注释互相引用即可。

需要裁剪的少数几处：worker health 租约矩阵（unit 7 参数已锁死判定逻辑，integration 4 状态可裁为抽查）；builtin discovery 的 slug 列表 oracle（两层各断言一次，保留 unit、integration 改断言落库事实）；knowledge 工具"私有库 404"三处同型（`test_knowledge_access_resolution.py`/`test_knowledge_tool_consumers.py`/`test_public_knowledge_tools.py`，已核实三处构造与断言同型，可参数化为边界矩阵）；`test_summary_middleware_real_model.py` 的压缩契约与 unit 版逐条对应，独有价值仅"真实 provider 接受压缩消息"，建议瘦身。

明确不应按重复处理的跨层对（含复核轮确证）：turn_references（纯函数 vs HTTP+PG+模型对端）、memory（纯函数 vs 跨连接锁串行化）、minio bucket race（错误码矩阵 vs 真实并发）、agent run lease 家族（SQLite 状态机 vs PG 锁）、steer/FIFO 三层（middleware 分支 vs PG 持久化事实 vs 真实 worker 调度）、milvus graph delete（unit 证 SQL 形状 vs 真实 Neo4j 行为）、milvus 高亮（unit 证标记协议 vs 真实 Milvus 打标）、agent feedback 三层（新增文件，api/services/e2e 各有增量）。

### I. 主题错置型

判据：测试内容健康，但放的文件/目录与被测域不符。

处理原则：搬家，不改内容。

典型：`integration/api/test_chat_router.py:902` 的 `test_standard_user_restores_visible_function_items_without_internal_audit` 直接 import e2e helper 跑完整 agent turn（e2e 场景放在 integration 层，行号经复核精确）；`unit/services/test_ocr_service.py` 末尾的 `test_resource_publication_reclaims_failed_or_cancelled_attempt` 测的是 documents/assets 上传回收（且是该函数全库唯一覆盖，必须迁移不能删）；`integration/services/test_thread_priority_inputs.py::test_auto_title_persists_once_and_preserves_manual_rename` 测标题生成（全库唯一覆盖，放错文件）；`unit/routers/test_knowledge_router_cleanup.py` 最后两个测试直接测 `knowledge_job_service`（L717/L731 起，应归 unit/services）；`unit/backends/test_semantic_chunking.py`（被测对象在 knowledge/chunking）；`unit/storage/test_agent_run_timing.py`（纯函数应归 unit/models）；`unit/services/test_model_cache.py`（被测对象在 models/providers，域错置）；`integration/api/test_shared_skill_edit_router.py` 的 `test_runtime_read_does_not_lock_unrelated_private_skill`（services 层测试放在 api 目录）。

### J. 耦合型（测试模块互相 import、顺序依赖、re-export 依赖）

判据：测试文件 import 另一个测试文件的 fixture/helper；或后一个测试依赖前一个测试留下的数据；或 patch 依赖源码 re-export 别名存活。

处理原则：共享物上提到 conftest/support；顺序依赖改为测试内自足并注释；统一 patch 定义模块。

典型（全景清单见第十二节 N6）：`e2e/test_agent_lifecycle_e2e.py` 被 6+ 个 e2e 文件当模块 import；`integration/api/test_chat_router.py:902` 与 `test_permission_convergence.py:72` 反向 import e2e 模块；integration/services 存在四文件 fixture 传递链（input_concurrency → input_schema → thread_priority_inputs → input_attachments）；integration/services 三个文件用 sys.path 裸 import `from test_schema_migration_version import ...`；`e2e/test_agent_bound_skill_e2e.py:24` 依赖另一 e2e 文件导出的 fixture。顺序依赖：`test_background_job_worker_path.py` 的 startup 测试依赖前测数据——复核确认这是**有意的 CI 编排**（`.github/workflows/system-tests.yml:126-156` 以 `BACKGROUND_JOB_GATE_ID` 串联三个测试跨容器重建 worker），不应按开发期遗留处置，应在测试内注释说明。

## 三、不建议清理的部分（避免误伤）

- **三层分层覆盖**：steer/FIFO、turn references、memory、agent run lease/manifest、minio bucket race、知识工具可见性、milvus graph delete、agent feedback 等主题的多层覆盖各有不可替代部分（见 H 类），不要按"重复"合并。
- **部署/配置契约测试**（`unit/config/` 四个 compose/nginx 文件）：守护挂载边界、容量默认值、槽位隔离等任何代码层测试都无法覆盖的部署面契约，且各带负控 guard 证明守卫自身有效。
- **oracle 元测试**（`test_openai_replay_contract.py`、`test_cooperation_replay_server.py`）：防止确定性 oracle 放水的守护测试，是证据链的一环。
- **墓碑测试**（断言已移除路由 404）：作为"防复活"guard 有价值，但散落面比初稿所列更大——复核轮实测至少 12 个文件含墓碑断言（新增 background_jobs_router 的 `/api/tasks*`、scheduled_agent_router 的 `/scheduled-agents`、knowledge_router 的 `/api/knowledge/databases*` 与 `/files/markdown`、public_session_items 的 `/history`、chat_router 文件内的 `/model-audits` 与 `/files*`），应集中（见第五节 #14），不应每处退役都留一个独立文件。
- **performance 域测试**：被测的 `test/performance/` 八个模块是仓库交付的压测工具，其契约核心是"不把失败/串绑伪装成成功"，为其写单测正当。

## 四、P0：真重复与死代码清单

### 4.1 场景真重复（删除/合并较弱一份）

| # | 保留 | 删除/合并 | 证据（均经复核确认） |
| --- | --- | --- | --- |
| 1 | `integration/api/test_public_knowledge_tools.py::test_knowledge_key_tool_route_boundary_without_kb` | `test_public_knowledge_key_boundary.py::test_knowledge_key_is_limited_to_public_knowledge_api` | 同为 knowledge key → list_kbs 200 → agents 403（boundary L27-40 / tools L30-35）；合并时保留 boundary 的 5 条管理路径 403 清单（L31-38）与 external 墓碑，以及 tools 独有的 download_kb_file 404（L36-41） |
| 2 | `integration/api/test_chat_router.py::test_public_thread_endpoints_require_authentication`（L54-57） | `test_public_agent_auth.py` 同名测试（L14-30） | 同名同主题，端点集合不相交（前者 GET sessions/audits/agent，后者 POST 创建/GET thread/POST events），合并为参数化 endpoint 列表 |
| 3 | `integration/api/test_upload_boundary.py::test_attachment_upload_preserves_metadata_and_minio_bytes`（L87-106） | `test_public_agent_files.py::test_draft_upload_does_not_create_session_and_deletes_original` 的保真段（L101-115） | 同端点 201、同查 agent_attachments（uid/status=draft/input_id=None）、同 MinIO 字节回读；后者独有 sessions 计数不变、响应黑名单、admin 404 矩阵、DELETE 语义 |
| 4 | `integration/services/test_agent_run_lease.py`（fencing 权威） | `test_agent_run_manifest_and_attempts.py` 的重复用例 | `match="lease owner"` 共 7 处（lease 91/236/323/435/462 + manifest 294/306/355），全部走 `repositories/runs.py:965` 同一 guard；`attempt.outcome == "lease_expired"` 两侧各一（lease L172 / manifest L161） |
| 5 | `integration/services/test_database_relation_integrity.py:125` | `test_agent_input_schema.py:492` 同约束断言 | `fk_messages_run_turn_session` 两侧各断言一次（初稿误写 498，498 是 `fk_agent_runs_output_message_scope`） |
| 6 | `integration/services/test_knowledge_projection_integrity.py::test_graph_parallel_vector_cancel_drains_both_branches_before_cleanup`（L428 起） | 同文件 `test_cancelled_external_writer_keeps_lock_until_io_settles` 的 graph 臂（L546-554） | advisory 锁 SQL L505-506 与 L584-585 逐字相同，断言序列同构；后者 graph 臂 monkeypatch 掉 `_build_pending_chunks`，是前者的高 mock 化弱化版；collection 臂（L536-545）独有 |
| 7 | `unit/services/test_checkpoint_state_reader.py` | `test_chat_service_sync.py` 的 3 个 `get_agent_state_view` 测试（def L1077/1104/1180） | 同被测函数、同 monkeypatch 对象（SessionRepository/AgentRunRepository/PublicItemRepository.list_items）；reader 侧用真实 InMemorySaver，是更强实现 |
| 8 | `unit/services/skills/test_skill_draft.py::test_failed_preparation_cannot_be_selected`（L68-69） | `test_skill_service.py::test_install_draft_loader_only_selects_ready_slugs`（L526-527） | 同函数 `load_and_select_draft_items`（`draft.py:152`）、同 `ValueError("不可安装")`（`draft.py:196`）；service 版额外覆盖正向选择 |
| 9 | `unit/plugins/test_ragflow_like_chunking.py::test_resolve_chunk_processing_params_priority`（L29-51） | `unit/knowledge/test_chunk_config_validation.py` 的优先级断言（L42-45） | 同一函数 `resolve_chunk_processing_params`（`presets.py:96-130`）的优先级契约分置两目录；坏值拒绝在 validation 文件 L14-21，ragflow 侧无 |
| 10 | `unit/knowledge/test_parser_facade.py` zip 族（5 测 + md 变体共 6） | `test_parser_capabilities.py::test_zip_parser_returns_markdown_text_without_sidecar_metadata`（L95-105） | 后者是弱化子集，但它是全库唯一直接断言 `extract_markdown_archive` 返回纯字符串（返回形状契约）的测试——删除前应先把该直接断言并入 facade zip 族 |
| 11 | `unit/storage/test_session_repository.py::test_only_state_proven_terminal_model_audit_keeps_tool_call_visible`（L230-334） | `unit/repositories/test_session_memory_history.py::test_memory_tools_exclude_unproven_or_active_model_audits`（L166-256） | run-active/unproven/proven 三 run 种子与"只有 proven 可见"断言近同构（run id 逐字相同）；读取入口不同，保留强的一份 |
| 12 | `unit/performance/test_matrix.py::test_formal_budget_and_explicit_small_experiment`（L52） | `test_cli.py::test_parser_preserves_matrix_budget_and_requires_command`（L22） | 同一 3150 预算 oracle 两处各算一遍 |
| 13 | `unit/performance/test_analysis.py::test_duplicate_groups_preserve_existing_outputs_before_refresh` | `test_cli.py`（L106-115）、`test_report.py`（L50-64）的同型断言 | "重复组拒绝"三现；"保留旧输出"仅 analysis 与 cli 两处成立（report 版测纯函数 render，无输出文件） |
| 14 | `e2e/test_agent_bound_skill_e2e.py`（参数化 scope） | `e2e/test_shared_skill_edit_e2e.py` | 相同 provider 注册块（bound L59-74 / shared L75-90）与"编辑→发版→SSE→manifest hash"主链逐段对应；shared 主链是 bound `(False,False)` 参数的子集；bound 独有版本 release/restore（L142-165） |

### 4.2 开发期遗留（直接删除/重写）

| # | 位置 | 问题 | 动作 |
| --- | --- | --- | --- |
| 1 | `unit/repositories/test_session_workdir.py` L35/L39 两个守卫 | 守护已删除的 `workdir_path` 参数 | 删 2 个显式守卫（另 2 个迁移期副产品酌情），剩余并入 `test_session_repository.py` |
| 2 | `unit/knowledge/test_knowledge_base_update.py:221`、`test_knowledge_runtime_cache.py:96`、`test_lazy_file_metadata.py:109` | `assert not hasattr(..., "_runtime_configs"/"files_meta")` 恒真 | 删断言 |
| 3 | `unit/services/test_chat_service_sync.py:48` `_fake_normalize_agent_context_config` | 死 helper | 删 |
| 4 | `unit/services/test_chat_stream_interrupt.py:14-25` `_FakeSession`/`_resolve_test_workdir` | 复制的死代码 | 删 |
| 5 | `integration/services/test_knowledge_stats_refresh.py::test_retired_stats_repair_route_cannot_mutate_persisted_facts`（L169-204） | 为已删路由起真实 uvicorn 断言 404 | 删（若需保留墓碑，并入集中式 removed-endpoints 测试） |
| 6 | `integration/services/test_database_access_paths.py:119,133` | `print(json.dumps(...))` 调试残留（初稿误写 117/131，117-118 是 Sort 断言） | 删 print；EXPLAIN 计划断言（L102-118）对 planner/统计敏感，加注释 |
| 7 | `e2e/test_milvus_text_retrieval_e2e.py:206-207` | 钉死 `pymilvus == "3.0.2"` | 改下限断言 |
| 8 | `unit/knowledge/test_parser_facade.py:217` | 永不触发的 skip | 删条件或修条件 |
| 9 | `unit/routers/test_mention_router.py` | mock 测 mock、漏测异常映射 | 重写为异常映射契约（L42-45 的 404/400 映射）或删除 |
| 10 | `unit/routers/test_skill_router.py` 12/14 测 | patch 掉权限闸门后仅断言转发，两个测试名与验证内容相反 | 重命名 + 重写为异常映射/最小 wiring，或砍到只保留 L397 正例模式（详见 G 类） |
| 11 | `test/live_api_cleanup.py` legacy 标题模式与 5 个孤儿 marker | 为已删测试文件的环境清理兼容层 | 设退役期限后删除 |
| 12 | `integration/services/test_background_job_worker_path.py` | 初稿存疑已被复核改判：`BACKGROUND_JOB_GATE_ID` 在 `.github/workflows/system-tests.yml:126-156` 有明确编排入口（跨容器重建 worker 的三段序列），属有意设计 | 保留；在测试内注释说明对前测数据与 CI gate 的依赖 |

## 五、P1：文件级合并清单

同被测对象/同 fixture 域的文件合并（31 组，预计净减约 50 个文件）。全部条目经复核确认方向成立，个别理由已按实测修正：

| # | 目标文件 | 并入来源 | 理由 |
| --- | --- | --- | --- |
| 1 | `unit/services/test_run_message_recorder.py`（基座） | `test_model_message_audit_service.py`、`test_tool_message_audit_service.py` | 三者 import 同一 `message_recorder` 模块（L11/L8/L8）、同构 helper；"duplicate start 保 monotonic" model 侧（model:161）与 tool 侧（tool:139）各一份；顺带修复两个旧模块名 |
| 2 | `unit/services/skills/test_skill_draft.py` | `test_skill_versions.py`（11 行/1 测） | 同属 skills 域目录（versions 测 `package.validated_shared_skill_parts`，draft 主测 `draft.py`） |
| 3 | `unit/services/test_session_thread_status.py` | `test_session_app_scope.py`（27 行/1 测） | 同 `threads` 模块（后者测 `mark_thread_viewed` 跨 APP 拒绝），sqlite fixture 可复用 |
| 4 | `unit/services/test_workspace_service.py` | `test_upload_stream_lifecycle.py`（53 行/1 测） | 同 `services.files` 模块 |
| 5 | `unit/services/test_background_jobs.py` | `test_background_job_registry.py`（13 行/1 测） | 同 background_jobs 模块族 |
| 6 | `unit/services/test_mcp_service.py` | `test_mcp_management_inspection.py`（62 行/2 测） | 同 `inspect_mcp_server_tools` 函数族，service 文件已有 inspection 测试 |
| 7 | `unit/repositories/test_agent_repository.py` | `test_agent_repository_delete_guard.py` | 同被测类 `AgentRepository`（前者 FakeDb 风格、后者 SQLite 风格） |
| 8 | `unit/storage/test_session_repository.py` | `unit/repositories/test_session_memory_history.py`、`test_session_workdir.py` 残余 | 三文件 import 同一 `SessionRepository`（`sessions.py`），被分裂在两个目录 |
| 9 | `unit/storage/test_minio_bounded_download.py` | `test_minio_public_images.py` 的 2 个 release 测试（L63-90/L95-122） | 同断言族（closed/released）；注意现状是三份各自手写的同型 fake-response（public_images 两份内联 + bounded 一份 `_Response`），合并时顺带收敛为一份 |
| 10 | `integration/api/test_evaluation_router.py` ← `test_evaluation_run_visibility_router.py`、`test_dataset_generation_resume_router.py` | 三文件同路由前缀 /api/evaluation、共享 conftest 的 `knowledge_base_resource`（`integration/conftest.py:255`）；清理 fixture 现状"三缺一且形态互异"（router 无、visibility `close_postgres_pool`、resume `reinit_pg_manager`） | 建议产出 `test_evaluation_api.py` 并统一清理 fixture |
| 11 | `integration/api/test_agent_bound_skill.py` ← `test_agent_bound_skill_versions.py` | 同被测对象；`current_content`（L32-41/L54-62）与 `close_projection_pool`（L24-29/L22-27）逐字双写；commit 失败补偿双写（bound L427-498 额外覆盖清理失败补偿）；zip helper 仅 bound 有（versions 经 self-skill 端点） | |
| 12 | `integration/api/test_viewer_filesystem_router.py` ← `test_viewer_filesystem_security.py` | `_create_thread`（L13-28）与 `_create_thread_for_user`（L15 起）逐行同型（同一 default agent → 建会话 → skip 容错），路径逃逸主题被切成两半 | |
| 13 | `integration/api/test_user_agent_env_router.py` ← `test_user_config_api.py`（29 行） | 同 `identity/users.py` 的两个 KV 端点（config L91-108 / agent-env L257-278），文件头部结构逐行同型 | |
| 14 | `integration/api/test_system_router_api.py` ← `test_settings_router.py`（16 行，仅断言已删路由 404，`yuxi/` 无 settings 路由已复核） | system 域契约 + 墓碑；更进一步把第三节所列 12+ 文件的墓碑断言集中为一个 removed-endpoints 文件 | |
| 15 | `integration/api/test_public_knowledge_tools.py` ← `test_public_knowledge_key_boundary.py` | 见 4.1#1；双方仅需 `test_client`+`admin_headers`，合并不引入新 fixture 工作 | |
| 16 | `integration/api/test_knowledge_access_resolution.py` + `test_knowledge_tool_consumers.py` + `test_public_knowledge_tools.py` 的"私有库不可见"场景 | 参数化为边界矩阵（凭据/端点/时序为参数），不强行删文件 | |
| 17 | `integration/api/test_file_mime_fd_reuse.py`（L73-101） ← `test_file_preview_limits.py`（L31-76） | 同 MinIO 上传 → documents/add → 断言 → 清理流程、同 `knowledge_base_resource` fixture | |
| 18 | `integration/services/test_agent_run_lease.py` ← `test_agent_run_manifest_and_attempts.py` | 同 `AgentRunRepository`、共用 `agent_run_test_helpers.py`（L26/L119，两文件分别 10/7 处调用）；顺带执行 4.1#4 的 7 处文案断言去重 | |
| 19 | `integration/services/test_state_reader_interrupt_integration.py` ← `test_steer_checkpoint_boundary.py` | 同 `object.__new__(PostgresManager)` + `AsyncConnectionPool` 装配样板（注意 `aget_tuple` 仅 steer 侧使用，state_reader 走 `graph.compile` + `_read_checkpoint_state`） | |
| 20 | `integration/services/test_project_workdir_provisioner.py` ← `test_workdir_user_workspace.py`（60 行/1 测） | 同 workdir/UserWorkspace 域（重叠仅在 `workspace.paths` 模块同源，合并理由按此修正） | |
| 21 | `integration/services/test_oidc_login_transaction.py` ← `test_password_login_boundary.py` | 同"登录 HTTP 事务边界"主题；`identity_db`/`oidc_http`（L34-66/L168-213）与 `login_boundary`（L30-90）三段结构（隔离 schema → 挂路由 → uvicorn sock + httpx）几乎逐行同型 | |
| 22 | `integration/services/test_model_provider_runtime_connectivity.py` ← `test_summary_middleware_real_model.py` | 两文件共享 `RUN_MODEL_PROVIDER_CONNECTIVITY` 门禁与同构 helper（`_select_enabled_model` L49-62 vs `_load_provider_config`+`_select_enabled_chat_model` L70-90，循环体近逐字）；`test_provider_reasoning_live.py` 门禁与 helper 均不同（`YUXI_REASONING_PROBE_MODELS`、`model_cache.get_model_info`），不并入，仅统一 env 约定 | |
| 23 | `integration/services/test_checkpoint_concurrency.py`（66 行/1 测） | 并入 checkpoint 主题文件（state_view 或 state_reader_interrupt）；与 `unit/agents/test_base_agent_checkpointer.py` 构成跨层对，unit mock 版价值更低 | |
| 24 | `e2e/test_agent_bound_skill_e2e.py` ← `e2e/test_shared_skill_edit_e2e.py` | Skill 来源参数化（bound/shared），见 4.1#14 | |
| 25 | `e2e/test_agent_async_e2e.py` ← `e2e/test_agent_call_entrypoints_e2e.py` | `_create_agent` 双双在 L26、函数体近逐字（仅 slug 前缀与期望常量不同）；run 持久化断言同构 asyncpg join | |
| 26 | `unit/config/test_runtime_directory.py` ← `test_runtime_capacity.py`（23 行） | 同一 `runtime_settings.py` 模块（get_int_env L8 / get_runtime_dir L20） | 产出 `test_runtime_settings.py` |
| 27 | `unit/utils/test_paths.py` ← `test_fd_io.py` | 同一 `infrastructure/filesystem.py`（copy_file_fd L112/write_all_fd L125/open_directory_fd L42/ensure_within_root L167）；顺带消除与 `unit/workspace/test_filesystem.py` 的撞名 | 产出 `test_filesystem.py`（infrastructure 域） |
| 28 | `unit/plugins/test_ragflow_like_chunking.py` ← `unit/knowledge/test_chunk_config_validation.py` | 同一函数契约，见 4.1#9 | |
| 29 | `unit/knowledge/eval/` ← `test_evaluation_task_cancellation.py` | 被测对象 `EvaluationService`（`evaluation/service.py:51`），同域单测全在 eval/；顺带收敛假仓储（见第七节 #4） | |
| 30 | `integration/graphs/test_graph_vector_projection_state.py` ← `integration/services/test_knowledge_projection_integrity.py` | 同被测仓储族（后者对 claim/mark/finalize 用 AsyncMock，前者是三函数唯一真实实现覆盖）；合并顺带替换其自置 `pg_manager._initialized` 单例 hack | 复核轮新增 |
| 31 | `integration/api/test_debug_message_projection.py` 的 audits 投影场景 → `test_chat_router.py`（audits 主题三文件散落：chat_router、agent_lifecycle_extended_e2e L71-78、debug_message_projection） | 场景不重复但主题应归一 | 复核轮新增 |

## 六、P1：拆分与文件内参数化清单

数量均经复核 grep 验证（个别已按实测修正）：

| 文件 | 规模 | 动作 |
| --- | --- | --- |
| `unit/services/skills/test_skill_service.py` | 2103 行/63 测 | 拆 shared 可见性/draft+import/projection/personal/builtin 五块；zip 导入族 5 测（+md 变体共 6）压缩为参数化（21 处内联 FakeRepo → 1 个工厂）；内置 spec 5 测（L601-642）表驱动；draft/edit 测试分别迁往 `test_skill_draft.py`/`test_skill_edit_service.py` |
| `integration/services/test_session_cooperation.py` | 1500 行/39 测 | 按目录可见性/创建幂等/等待恢复/停止取消租约沙盒释放拆 3-4 文件，`tree` fixture 下沉目录 conftest |
| `unit/sandbox/test_sandbox_backends.py` | 1829 行/77 测 | module fixture 替代 49 处 stub（其中字面 `lambda: object()` 47 处）；read/aread 错误矩阵参数化（错误串 5+2 处）；sync/async 成对测试合并 |
| `unit/sandbox/test_sandbox_provisioner_config.py` | 1824 行/61 测 | `_load_module()`×56 与 `PROVISIONER_BACKEND`×38 收敛为 module fixture；k8s sys.modules stub 块×4（两两逐字）收敛；docker/k8s 同构测试参数化 |
| `unit/services/test_chat_service_sync.py` | 1313 行 | state view 3 测迁出；4 个 workspace prompt 测（L1019-1228）参数化；10 个 save_messages 测试（调用行 340-995）抽共享 Fake 工厂（现 23 个 Fake 类继承 `_FakeDBBase/_FakeRunRepoBase`，每测体 52-89 行）；删死 helper |
| `unit/graphs/test_milvus_graph_build.py` | 1375 行/42 测 | 拆 normalization/llm_extractor/build_pipeline/query_and_cypher 四文件；kb 配置 SimpleNamespace 7 处（前 5 同模板）与 ChunkRepo 假类 5 份收敛；timeout 双测（L307/333）参数化 |
| `integration/api/test_knowledge_router.py` | 985 行/31 测 | 按 CRUD/文件夹/query-params/ACL/退役五主题重组；Dify 创建三连、403 组、退役三测参数化；部门/用户 helper 与 auth_router 共享 |
| `integration/api/test_chat_router.py` | 951 行/21 测 | artifact-save 4 测参数化；viewed/tool_approval 并入同端点测试；L60 起 287 行长测试拆分（见 N10）；e2e 错位测试迁出 |
| `integration/services/test_background_job_repository.py` | 849 行/20 测 | 3 组镜像用例参数化（L192/221 锁等待、L433/485 dataset、L612/668 run 收敛）；KB seed 样板 10 处（L197-675）提 helper |
| `unit/middlewares/test_summary_middleware.py` | 1155 行/26 测 | 同模板对实证：L1000 vs L1043（前者是后者参数化矩阵的未参数化格子）、L722 vs L968 同骨架；先补齐再参数化 |
| `unit/services/test_run_worker.py` | 1749 行/54 测 | 按被测对象拆 runner/bootstrap/settings（3 个 subprocess 测试）/event_writer 四块 |
| `unit/repositories/test_agent_run_repository.py` | 1017 行/23 测 | 按租约/attempt/manifest+timing/cancel 分节；删 L735/737 连续两条 `assert child.status == "running"` |
| `unit/services/test_model_selectors.py` | 785 行/31 测 | embedding sync/async 矩阵参数化（现 5 测且 async 侧无 5xx 对应——矩阵本身不完整） |
| `unit/knowledge/test_parser_facade.py` | 561 行/28 测 | 按引擎/文档服务/zip 产物拆三份；zip URL 编码 3 测参数化；uv.lock 检查迁出单测；删 `_build_docx`/`_build_png` 死 helper |
| `e2e/test_agent_lifecycle_e2e.py` | 1634 行/13 测 | helper 抽到 e2e_helpers 后分组；FIFO 测试（L306-541，236 行/≥4 场景）内 SSE resync 段并入 SSE 分组；cancel-waiting（L1023-1154）与 rate-limit（L1351-1430）同骨架参数化失败类型 |
| `e2e/test_milvus_text_retrieval_e2e.py` | 502 行/1 测 | 单函数约 436 行拆 2-3 个测试共享索引 fixture |
| `unit/toolkits/test_kbs_tools.py` | 905 行/27 测 | 严格三件套（`_patch_retrievers` + visible-kbs patch + 空 runtime）10 处、宽松双件套 19 处提 fixture；5 个 `_run_*` wrapper 合一；删 L731/771 的 `del tmp_path` |

## 七、P1：脚手架/fixture 收敛清单

1. **integration no-op 清理 fixture**：`ensure_live_api_schema`/`cleanup_test_knowledge_resources`/`cleanup_test_sandboxes` 空定义共 **55 处**（16/19/20，主要在 integration/services，另有 integration/api 3 处；复核轮发现 staged 新文件 `test_agent_feedback.py` 仍在复制该模板）。改为一个显式 marker（如 `isolated_schema`）在 conftest 统一判断。
2. **sqlite 内存引擎 fixture**：**21 个文件**手写 `create_async_engine("sqlite+aiosqlite:///:memory:")`（unit/repositories 6、unit/services 7、unit/routers 3、unit/config 2、unit/knowledge 1、unit/storage 1、integration/services 1），而 `test/support/sqlite.py::create_utc_sqlite_engine` 仅 3 个文件在用。统一。
3. **`FakeKnowledgeBase(KnowledgeBase)` 存根**：近逐字相同的 3 份（`test_lazy_file_metadata.py:8-43`、`test_knowledge_base_update.py:11-43`、`test_office_pdf_preview.py:13-48`）上提 `unit/knowledge/conftest.py`；`test_file_size_fallback.py:58-75` 是局部 `TestKB`，可一并统一。
4. **eval 域假对象**：`FakeEvaluationRepository` 三份同名不同体（`eval/test_result_filters.py:24`、`eval/test_service_generation.py:10`、`unit/knowledge/test_evaluation_task_cancellation.py:31`——第三份在 eval/ 目录外）；`FakeContext` 两份形状不同；逐字重复的只有 autouse `fake_chunk_repository`（benchmark:38-49 与 resume:16-27）——上提 eval/conftest。
5. **public_* 系列的 apikey 样板**：12 个文件重复"POST `/api/user/apikey/` → headers → finally DELETE"；`delete_created_threads`（`test_public_agents_key_boundary.py:18`）被 **4 个文件**导入（public_session_items/public_session_resources/public_agent_files/debug_message_projection）。提取 `apikey_factory` 并上移 helper。
6. **e2e helper 归一**：extended 文件 L415-512 平行实现 5 个 helper（`_provider`/`_agent` 与母文件 L38-108 近逐字，`_delete_provider`/`_create_thread` 母文件无对应、系测试体内联）；async 与 entrypoints 的 `_create_agent` 逐字；附件三件套各写一份上传+`tmp/chat_attachments` 清理；`postgres_dsn()` 双份逐字（`e2e_helpers.py:30-31` / `live_api_cleanup.py:139-141`）；feedback 新文件又复制了 provider 注册块。
7. **replay server**：`test_permission_revocation_e2e.py` 的 `replay` fixture（L67-190，约 124 行 Handler）并入 `support/openai_replay_server.py`；对 `test.integration.api.test_permission_convergence` 的跨层 import 在 L20-22。
8. **unit/config**：`_project_root` 四文件逐字（checkpointer:10/service_boundaries:53/worktree_slots:43/nginx:8）提 conftest；`_load_compose` 仅 2 处（checkpointer 内联 `yaml.safe_load`）；checkpointer 文件 5 个 for 循环（L22/31/115/127/140）改参数化。
9. **部门/用户创建 helper**：`test_auth_router.py:88-115` 与 `test_knowledge_router.py:27-89` 两处 helper 复制（同 `/api/departments` + `Pw!{suffix}` 流程）；`test_department_router.py` 为测试体内联同型。提取共享 helper。
10. **marker 双维护**：`test/conftest.py` 与 `pyproject.toml` 各注册 5 个 marker；unit/auth/slow 三条描述逐字一致，integration/e2e 两条描述不一致——先统一文案再收敛到一处。
11. **`_create_thread` 七处定义**（复核轮新增）：e2e 侧 async:65 ≈ read_file_multimodal:64（约 90% 同构）≈ extended:481；integration 侧 viewer_router:13 ≈ viewer_security:15、skill_artifact_authorization:22（含 `agent.get("agent_id")` 死分支）、chat_router:488。统一上提。
12. **`make_file_record` 四份**（复核轮新增）：`test_lazy_file_metadata.py:49` ≈ `unit/plugins/test_milvus_kb.py:90`（约 22 字段同构）、`test_knowledge_base_update.py:66` 变体、`test_office_pdf_preview.py:74` 子集。

13. **大文件预览的跨入口样板**：`unit/services/test_workspace_service.py::test_workspace_preview_rejects_large_source_before_content_preparation` 与 `test_viewer_filesystem_service.py` 的同名用例脚手架相似，但分别覆盖 `workspace/services/files.py` 与 `viewer.py` 的预算传递边界。可合并为跨入口参数化测试，必须保留两个入口，不能择一删除覆盖。

## 八、P2：命名与归位清单

**更名**（名不符实）：

| 现名 | 问题 | 建议 |
| --- | --- | --- |
| `unit/services/test_model_message_audit_service.py` / `test_tool_message_audit_service.py` | 旧模块别名 | 三合一后随新文件名消失 |
| `unit/services/test_chat_service_sync.py` | 四主题杂烩 | 拆分后按主题命名 |
| `unit/services/test_run_worker.py` | 1/4 主题 + `as run_worker` 别名把旧模块名硬编码 | 拆分后按被测对象命名 |
| `unit/services/test_channel_command_service.py` | 测的是 `agents/services/commands.parse_slash_command` | `test_agent_commands.py` |
| `unit/services/test_base_agent_langfuse_config.py` | 5 测仅 1 测（L72）涉及 langfuse | `test_base_agent_config.py` |
| `unit/services/test_chat_service_langfuse_stream.py` | 多数测试与 langfuse 无关 | `test_chat_service_stream.py` |
| `unit/knowledge/test_file_listing_scaling.py` | 10 测无任何 scaling 断言 | 并入 manager 族后消失 |
| `unit/knowledge/test_knowledge_manager_ownership.py` | ownership/effective_permission 零命中 | 同上 |
| `unit/knowledge/test_parser_facade.py` | 解析域杂烩 | 拆分后按主题命名 |
| `unit/routers/test_knowledge_router_cleanup.py` | "cleanup" 来自历史重构 | `test_knowledge_documents_router.py` |
| `unit/routers/test_mention_router.py` | 测试名 "live_project_viewer" 名不副实 | 随重写消失 |
| `e2e/test_openai_events_e2e.py` | 测默认模式合批/取消 | `test_default_mode_batching_e2e.py` |
| `e2e/test_agent_lifecycle_extended_e2e.py` | "extended" 无语义 | `test_agent_run_audit_e2e.py` |
| `e2e/test_mcp_stdio_security.py` | 目录唯一无 `_e2e` 后缀 + 仅裸 e2e 标记 | 加后缀并补全标记 |
| `unit/utils/test_paths.py` | 测 `infrastructure/filesystem`，与 workspace 域 `test_filesystem.py` 撞名 | 随第五节 #27 合并消失 |
| `unit/services/test_agents_history_metadata.py` | 实测 `public_items.serialize_public_items`（`public_items.py:75`），无 agents_history_metadata 模块 | `test_public_items_projection.py` |
| `unit/services/test_model_cache.py` | 被测对象在 `models/providers/cache.py`，域错置 | 移 models 域 |
| `unit/services/test_chat_attachment_context.py` | 测 `execution.py` 私有函数 `_with_attachment_context` | 更名指向被测对象 |
| `unit/services/test_worker_health.py` vs `integration/services/test_worker_health_redis.py` | 同一 `workers/health.py` 的成对测试命名不对称 | 统一命名（如 unit 侧 `test_worker_health_probe.py`） |

**归位**（搬家不改内容）：`unit/` 根目录 8 个文件——`test_chunking_token_limit.py`、`test_qa_parser.py` → `unit/knowledge/`；`test_auth_utils.py` → `unit/identity/`；`test_cooperation_replay_server.py`、`test_openai_replay_contract.py`、`test_e2e_wait_budget.py`、`test_live_api_cleanup.py` → 新建 `unit/support/`（`test_e2e_wait_budget.py` 顺带更名 `test_e2e_helpers.py`）；`test_package_import.py` 保留。另有 `unit/agent_context_fixtures.py`（无 `test_` 前缀的非测试共享模块，被 chat_service_sync/langfuse_stream/run_worker 三文件 import，见 N6）应迁 `unit/support/` 或并入 unit/conftest。其余归位项见 I 类。

**归组保留的小微文件**（不删，但不再新增同类）：`unit/repositories/test_agent_state_repository.py`（46 行）、`test_skill_repository.py`（31 行）、`test_knowledge_chunk_repository.py`（54 行）、`unit/storage/test_neo4j_manager.py`（31 行）、`unit/toolkits/test_tool_registry.py`（18 行）、`unit/agents/test_chatbot_prompt.py`（23 行）、`test_base_agent_checkpointer.py`（25 行）、`unit/utils/test_string_utils.py`（28 行）——各覆盖独立契约，等对应域增加行为测试时并入。

## 九、建议的目标结构（示意）

```
test/
├── conftest.py                  # 仅 pytest 插件（marker 归 pyproject，文案先统一）
├── support/                     # + cleanup.py（原根目录 live_api_cleanup.py）
├── unit/
│   ├── identity/  knowledge/  middlewares/  ...
│   ├── infrastructure/          # test_filesystem.py（fd_io+paths 合并）等
│   ├── support/                 # replay 元测试、cleanup 单测、e2e helper 单测、agent_context_fixtures
│   └── services/                # message_recorder 三合一等
├── integration/
│   ├── api/                     # evaluation_api、bound_skill、viewer_filesystem、public_knowledge_tools 等
│   ├── services/                # run_lease、state_reader、登录边界、provider 探针等合并件；session_cooperation 拆 3-4
│   ├── graphs/  identity/  sandbox/   # 原有目录保留；graphs 两个文件择机并入 projection 主题
│   └── ...
└── e2e/                         # bound_skill（参数化 scope）、agent_smoke（async+entrypoints 合并）等
```

合并后预计测试文件数从约 340 降到约 290，总行数下降约 2000-3000（全部来自样板与死代码），测试函数与断言不减少。

## 十、逐文件索引

判定取值：保留 / 合并→目标 / 拆分 / 更名 / 归位 / 清理（死代码或标注）。（重写）表示内容健康但需改写 oracle。

### 10.1 根目录与 support

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `conftest.py` | 22 行 | 清理：marker 注册与 pyproject 收敛到一处（文案先统一） |
| `live_api_cleanup.py` | 759 行 | 实现（非测试）；归位 `support/cleanup.py`；legacy 兼容层设退役期限 |
| `support/sqlite.py` | 28 行 | 保留；扩大采用面（现仅 3 文件在用，21 个文件手写） |
| `support/openai_replay_server.py` | 351 行（staged 修改后） | 保留；吸收 e2e 本地 Handler |
| `support/public_events.py` | 27 行 | 保留 |
| `support/cooperation_replay_server.py` | 271 行 | 保留 |

### 10.2 unit/ 根散落（8 + 1 共享 fixture）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_auth_utils.py` | 192 行/14 测 | 归位 `unit/identity/`；JWT issuer 前缀字面量四处（L104-173）改用源码常量 |
| `agent_context_fixtures.py` | — （非测试共享模块，无 `test_` 前缀） | 归位 `unit/support/` 或并入 unit/conftest（被 3 个测试文件 import） |
| `test_chunking_token_limit.py` | 182 行/16 测 | 归位 `unit/knowledge/` |
| `test_cooperation_replay_server.py` | 129 行/4 测 | 归位 `unit/support/` |
| `test_e2e_wait_budget.py` | 20 行/1 测 | 归位 `unit/support/` 并更名 `test_e2e_helpers.py` |
| `test_live_api_cleanup.py` | 578 行/20 测 | 归位 `unit/support/` |
| `test_openai_replay_contract.py` | 140 行/6 测 | 归位 `unit/support/` |
| `test_package_import.py` | 12 行/1 测 | 保留（包级懒加载契约；且是 monkeypatch.setenv 的正面样板） |
| `test_qa_parser.py` | 229 行/23 测 | 归位 `unit/knowledge/` |

### 10.3 e2e（21 测试文件 + helpers/conftest）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_agent_lifecycle_e2e.py` | 1634 行/13 测 | 保留（母文件）；抽 helper、分组、2 处参数化；集中 replay host 配置（当前容器拓扑两者均可达）；补 timeout |
| `test_agent_lifecycle_extended_e2e.py` | 504 行/4 测 | helper 并回母文件；更名 |
| `test_agent_lifecycle_key_scope_e2e.py` | 172 行/1 测 | 保留；统一 import 风格 |
| `test_agent_async_e2e.py` | 263 行/1 测 | 合并→与 entrypoints；补 timeout |
| `test_agent_call_entrypoints_e2e.py` | 203 行/1 测 | 合并→与 async；补 timeout |
| `test_agent_bound_skill_e2e.py` | 271 行/1 测×3 参数 | 吸收 shared_skill_edit，参数化 scope；解除对 permission_revocation 的 fixture import |
| `test_shared_skill_edit_e2e.py` | 190 行/1 测 | 合并→bound_skill |
| `test_agent_feedback_e2e.py` | 153 行/1 测×2 参数（staged 新文件） | 保留（CI 已接线）；provider/agent 注册与 replay fixture 并入 lifecycle helpers |
| `test_document_parsing_artifacts_e2e.py` | 357 行/2 测 | 保留；附件清理样板抽 fixture；补 timeout/slow |
| `test_draft_attachments_e2e.py` | 167 行/1 测 | 保留 |
| `test_input_projection_recovery_e2e.py` | 183 行/1 测×3 参数（主审期间并行提交） | 保留：spawn 真实进程 + `os._exit` 模拟消费后崩溃，验证补投/租约过期两条恢复路径，是 e2e 层独有手段；正确 import lifecycle helpers（未复制）；与 run_lease/input_concurrency 为分层互补 |
| `test_mcp_stdio_security.py` | 125 行/4 测 | 更名加 `_e2e`；补全标记；2 个进程内 builtin 测试迁 integration；stdio 拒绝与 API 层重复存疑，保留一层 |
| `test_milvus_text_retrieval_e2e.py` | 502 行/1 测 | 拆 2-3 测；删版本钉死；补 timeout/slow |
| `test_ocr_config_center_e2e.py` | 182 行/1 测 | 保留；补 timeout/slow |
| `test_openai_events_e2e.py` | 249 行/3 测 | 更名；分页段与 lifecycle 去重 |
| `test_permission_revocation_e2e.py` | 756 行/7 测 | replay Handler 并入 support；actors fixture 上移；解除 e2e→integration import |
| `test_personal_skill_agent_e2e.py` | 179 行/1 测 | 保留；agent 创建脚手架入 helpers；补 timeout |
| `test_provider_reasoning_e2e.py` | 149 行/1 测（env 门控） | 保留；与 integration 探针统一 env 约定；补 timeout |
| `test_read_file_multimodal_e2e.py` | 257 行/2 测（env 门控） | 保留；补 timeout |
| `test_session_config_e2e.py` | 160 行/1 测 | 保留 |
| `test_session_cooperation_e2e.py` | 457 行/2 测 | 保留 |
| `e2e_helpers.py` | 112 行 | 保留；DSN 去重；429 静默 skip（L27）改为显式报告 |
| `e2e/conftest.py` | 133 行 | 保留 |

### 10.4 integration/api（52）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_agent_bound_skill.py` | 535 行/7 测 | 吸收 versions（合并目标） |
| `test_agent_bound_skill_versions.py` | 473 行/8 测 | 合并→bound_skill |
| `test_agent_config_resource_authorization.py` | 519 行/5 测 | 保留；L168 起 199 行长测试拆分 |
| `test_agent_create_resources.py` | 345 行/7 测 | 保留（参数化范本） |
| `test_agent_feedback.py` | 215 行/4 测（staged 新文件） | 保留；apikey 三连样板随第七节 #5 收敛 |
| `test_apikey_router.py` | 320 行/15 测 | 保留；401 组与 CRUD 参数化 |
| `test_auth_router.py` | 438 行/13 测 | 保留；锁定三测合并；部门 helper 共享 |
| `test_background_jobs_router.py` | 235 行/7 测 | 保留；边界 403/404 参数化；`/api/tasks*` 墓碑集中 |
| `test_chat_router.py` | 951 行/21 测 | 保留+整改：内部参数化、L60 起 287 行长测试拆分、e2e 错位测试迁出、401 与 public_agent_auth 合一 |
| `test_checkpoint_state_view.py` | 97 行/1 测 | 保留；跨用户 404 与 cooperation_state_recovery 择一参数化 |
| `test_context_compression_router.py` | 190 行/1 测 | 保留；L38 起 153 行长测试拆分 |
| `test_dashboard_router.py` | 258 行/12 测 | 保留；统计 schema 4 测参数化 |
| `test_dataset_generation_resume_router.py` | 112 行/2 测 | 合并→evaluation_api |
| `test_debug_message_projection.py` | 139 行/1 测（新增未入 CI） | audits 场景并入 chat_router 或组成 seed 矩阵；决定是否入 CI |
| `test_department_router.py` | 99 行/2 测 | 保留；helper 共享 |
| `test_evaluation_router.py` | 61 行/3 测 | 合并目标 evaluation_api |
| `test_evaluation_run_visibility_router.py` | 54 行/1 测 | 合并→evaluation_api |
| `test_file_mime_fd_reuse.py` | 110 行/3 测 | 合并目标（MIME/预览投递） |
| `test_file_preview_limits.py` | 76 行/2 测 | 合并→file_mime；30MB 文案与 unit 侧共享常量 |
| `test_graph_build_status.py` | 25 行/1 测 | 并入 knowledge_router；断言过弱；补 marker |
| `test_knowledge_access_resolution.py` | 63 行/1 测 | 知识工具边界矩阵参数化 |
| `test_knowledge_router.py` | 985 行/31 测 | 主题重组+4 组参数化；吸收 graph_build_status；墓碑集中 |
| `test_knowledge_tool_consumers.py` | 119 行/2 测 | 知识工具边界矩阵参数化 |
| `test_mcp_router.py` | 232 行/3 测 | 保留（integration 是校验超集） |
| `test_model_provider_uid_header.py` | 164 行/1 测 | 保留 |
| `test_module_failure_boundaries.py` | 308 行/5 测 | 保留；2 个 corrupt share_config 参数化 |
| `test_permission_convergence.py` | 627 行/12 测 | 保留 |
| `test_personal_skill_router.py` | 71 行/1 测 | 保留；补 marker |
| `test_project_api.py` | 345 行/7 测 | 保留；删 `_create_lifecycle_session` 死 helper（L110） |
| `test_public_agent_auth.py` | 150 行/5 测 | 401 与 chat_router 合一；其余保留 |
| `test_public_agent_files.py` | 179 行/4 测 | 保留；附件保真段与 upload_boundary 去重；改用上移后的 delete_created_threads |
| `test_public_agents_key_boundary.py` | 342 行/5 测 | 保留；CORS 测试独立；helper 上移 |
| `test_public_end_user.py` | 141 行/1 测 | 保留；X-End-User-Id 403 三处参数化 |
| `test_public_knowledge_key_boundary.py` | 89 行/4 测 | 合并→public_knowledge_tools |
| `test_public_knowledge_tools.py` | 239 行/5 测 | 合并目标 |
| `test_public_session_items.py` | 256 行/3 测 | 保留 |
| `test_public_session_resources.py` | 128 行/2 测 | 保留 |
| `test_scheduled_agent_api.py` | 193 行/1 测 | 保留；L35 起 159 行长测试拆分 |
| `test_session_cooperation_state_recovery.py` | 188 行/1 测 | 保留 |
| `test_settings_router.py` | 16 行/1 测 | 并入 system_router_api 或墓碑集中 |
| `test_shared_skill_edit_router.py` | 308 行/4 测 | 保留；1 个 services 层测试迁出 |
| `test_skill_artifact_authorization.py` | 136 行/1 测 | 保留；`_create_thread` 上提；删 `agent.get("agent_id")` 死分支 |
| `test_system_router_api.py` | 347 行/14 测 | 吸收 settings_router |
| `test_turn_references.py` | 653 行/10 测 | 保留（slow 探针显式注明） |
| `test_turn_result_causality.py` | 144 行/1 测 | 保留；fixture 与 turn_references 共享 |
| `test_unified_graph_router.py` | 102 行/6 测 | 保留 |
| `test_upload_boundary.py` | 146 行/4 测 | 保留；附件保真二选一 |
| `test_user_agent_env_router.py` | 71 行/5 测 | 合并目标（user prefs） |
| `test_user_config_api.py` | 29 行/2 测 | 合并→user_agent_env |
| `test_viewer_filesystem_router.py` | 156 行/4 测 | 合并目标（含 security） |
| `test_viewer_filesystem_security.py` | 123 行/4 测 | 合并→viewer_router |
| `test_workspace_runtime_cache_api.py` | 63 行/1 测 | 保留 |

### 10.5 integration/services（38）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_agent_feedback.py` | 337 行/8 测（staged 新文件） | 保留（CI 已接线）；停止复制 no-op fixture 模板 |
| `test_agent_input_concurrency.py` | 400 行/6 测 | 保留；fixture 链上提（见 N6） |
| `test_agent_input_schema.py` | 622 行/6 测 | 约束负向移交 relation_integrity（L492）；迁移测试移交 schema_migration_version |
| `test_agent_run_lease.py` | 578 行/9 测 | 合并目标（含 manifest_attempts） |
| `test_agent_run_manifest_and_attempts.py` | 400 行/7 测 | 合并→run_lease |
| `test_api_key_user_lifecycle.py` | 161 行/2 测 | 保留 |
| `test_arq_worker_dispatch.py` | 154 行/3 测 | 保留 |
| `test_background_job_repository.py` | 849 行/20 测 | 保留；3 组参数化+seed helper |
| `test_background_job_worker_path.py` | 149 行/3 测 | 保留；注释说明 CI gate 编排（system-tests.yml:126-156）与前测数据依赖 |
| `test_builtin_discovery.py` | 215 行/4 测 | 保留；slug oracle 两层择一 |
| `test_checkpoint_concurrency.py` | 66 行/1 测 | 合并→checkpoint 主题 |
| `test_chunk_config_boundary.py` | 236 行/8 测 | 保留；4 个无需 HTTP 的测试轻量化 |
| `test_database_access_paths.py` | 133 行/2 测 | 清理 print（L119/133）；oracle 脆弱标注；裸 import 修复 |
| `test_database_relation_integrity.py` | 387 行/7 测 | 吸收约束负向；2 测主题迁出；裸 import 修复 |
| `test_identity_admin_service.py` | 135 行/2 测 | 保留 |
| `test_input_attachments.py` | 361 行/7 测 | 保留 |
| `test_knowledge_projection_integrity.py` | 601 行/10 测 | 保留；graph 取消臂去重；吸收 graphs/test_graph_vector_projection_state |
| `test_knowledge_stats_refresh.py` | 258 行/4 测 | 删退役路由 guard 测试，其余保留 |
| `test_live_api_cleanup_run_rows.py` | 604 行/11 测 | 保留（实现/mock 单测/真库集成三层分工的标准案例） |
| `test_memory_service.py` | 253 行/3 测 | 保留；"Memory 已关闭" 文案两层共享常量 |
| `test_minio_bucket_creation.py` | 67 行/1 测 | 保留（与 unit 版分层互补） |
| `test_model_provider_runtime_connectivity.py` | 143 行/3 测 | 合并目标（provider live 探针） |
| `test_oidc_login_transaction.py` | 248 行/5 测 | 合并目标（登录边界） |
| `test_password_login_boundary.py` | 191 行/5 测 | 合并→oidc_login_transaction |
| `test_project_thread_archive.py` | 239 行/2 测 | 保留 |
| `test_project_workdir_provisioner.py` | 364 行/6 测 | 吸收 workdir_user_workspace |
| `test_provider_reasoning_live.py` | 93 行/1 测（env 门控） | 保留；**补 `integration` 标记（全文零标记，实测）**；与 e2e 探针统一 env 约定 |
| `test_run_stream_redis.py` | 45 行/1 测 | 保留（可并入 Redis 集成主题） |
| `test_scheduled_agent_repository.py` | 534 行/4 测 | 保留；装配代码抽 factory；L55/271 长测试拆分 |
| `test_schema_migration_version.py` | 456 行/9 测 | 保留；吸收 input_schema 迁移测试 |
| `test_session_cooperation.py` | 1500 行/39 测 | 拆 3-4 文件 |
| `test_state_reader_interrupt_integration.py` | 148 行/3 测 | 合并目标（checkpoint 行为） |
| `test_steer_checkpoint_boundary.py` | 162 行/1 测 | 合并→state_reader |
| `test_summary_middleware_real_model.py` | 161 行/1 测 | 瘦身为"真实模型兼容"后并入 provider 探针 |
| `test_thread_priority_inputs.py` | 638 行/11 测 | 保留；title 测试迁出 |
| `test_user_skill_projection.py` | 268 行/1 测（内含 221 行长测试） | 保留；拆 2-3 个测试 |
| `test_workdir_user_workspace.py` | 60 行/1 测 | 合并→project_workdir_provisioner |
| `test_worker_health_redis.py` | 69 行/3 测 | 保留；4 状态矩阵裁为抽查 |

### 10.6 integration/graphs（2）+ identity（1）+ sandbox（1）——复核轮补审

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `graphs/test_graph_vector_projection_state.py` | 182 行/2 测 | 并入→test_knowledge_projection_integrity（三函数唯一真实实现覆盖，合并不丢覆盖）；补文件级 marker |
| `graphs/test_milvus_graph_delete.py` | 51 行/1 测 | 保留（与 unit 层 SQL 形状测试分层互补）；决定是否入 CI |
| `identity/test_identity_permission_constraints.py` | 57 行/1 测 | 保留（CI 已接线，约束唯一覆盖） |
| `sandbox/test_sandbox_native_grep.py` | 89 行/1 测 | 保留（与 unit grep 族刻意分层）；**补 `integration` marker（与 `test_provider_reasoning_live.py` 同为仅有的两个筛选缺口）**；TEST_SANDBOX_URL 无配置来源，注明维护口径 |

### 10.7 unit/services（62 + skills 5）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_agent_artifacts_state.py` | 84 行/5 测 | 保留 |
| `test_agent_config_service.py` | 100 行/3 测 | 保留 |
| `test_agent_input_messages.py` | 122 行/11 测 | 保留；11 个中文测试名统一为英文 |
| `test_agent_lifecycle_services.py` | 98 行/5 测 | 更名主题化；waitpoint 参数化 |
| `test_agent_preparation.py` | 340 行/11 测 | 保留 |
| `test_agent_scheduler_recovery.py` | 124 行/1 测 | 保留 |
| `test_agents_history_metadata.py` | 47 行/3 测 | 更名 `test_public_items_projection.py` |
| `test_agent_transport.py` | 255 行/9 测 | 保留；L160 best-effort 测试加注释 |
| `test_arq_worker.py` | 26 行/1 测 | 归位 workers 域 |
| `test_artifact_service.py` | 381 行/16 测 | 保留；save 族参数化 |
| `test_attachment_service.py` | 371 行/10 测 | 保留；移除导入期 env 副作用（含 `YUXI_RUNTIME_DIR`）；删 L163 死 helper |
| `test_auth_service_cli_auth.py` | 123 行/5 测 | 保留 |
| `test_background_job_registry.py` | 13 行/1 测 | 合并→background_jobs |
| `test_background_jobs.py` | 550 行/20 测 | 合并目标 |
| `test_base_agent_langfuse_config.py` | 158 行/5 测 | 更名；正反例参数化 |
| `test_channel_command_service.py` | 20 行/3 测 | 更名 `test_agent_commands.py` |
| `test_chat_attachment_context.py` | 42 行/3 测 | 更名指向被测对象 |
| `test_chat_service_langfuse_stream.py` | 657 行/16 测 | 更名；跨文件 import 收敛 |
| `test_chat_service_sync.py` | 1313 行/24 测 | 大整改（见第六节） |
| `test_chat_stream_interrupt.py` | 75 行/4 测 | 清理死代码 |
| `test_checkpoint_state_reader.py` | 135 行/4 测 | 吸收 sync 的 state view 3 测 |
| `test_context_compression_service.py` | 308 行/6 测 | 保留 |
| `test_dashboard_service.py` | 494 行/9 测 | 保留；sqlite fixture 统一 |
| `test_knowledge_access.py` | 38 行/3 测 | 保留 |
| `test_knowledge_dashboard_service.py` | 37 行/1 测 | 保留（并入 knowledge 域可选） |
| `test_langfuse_service.py` | 296 行/12 测 | 保留；URL 拒绝型 3 测（L263/272/281）参数化 |
| `test_login_rate_limit_service.py` | 162 行/7 测 | 保留（L95/109 是正控断言，非弱断言） |
| `test_mcp_management_inspection.py` | 62 行/2 测 | 合并→mcp_service |
| `test_mcp_manifest.py` | 55 行/3 测 | 保留 |
| `test_mcp_service.py` | 483 行/18 测 | 合并目标 |
| `test_memory_service.py` | 248 行/10 测 | 保留；三件套 fixture 抽取 |
| `test_mention_search_service.py` | 128 行/4 测 | 保留 |
| `test_model_cache.py` | 144 行/4 测 | 归位 models 域 |
| `test_model_message_audit_service.py` | 243 行/4 测 | 三合一→message_recorder |
| `test_model_provider_service.py` | 560 行/26 测 | 保留；拒绝族参数化；移除导入期 env 副作用 |
| `test_model_selectors.py` | 785 行/31 测 | 保留；embedding 矩阵参数化 |
| `test_ocr_service.py` | 156 行/5 测 | 保留；末尾错放测试迁 documents/assets |
| `test_oidc_service.py` | 153 行/4 测 | 保留；移除导入期 env 副作用 |
| `test_openai_event_adapter.py` | 232 行/7 测 | 保留（oracle 范本） |
| `test_project_service.py` | 336 行/13 测 | 保留 |
| `test_public_agents_api.py` | 128 行/8 测 | 保留 |
| `test_readiness_service.py` | 231 行/8 测 | 保留 |
| `test_run_message_recorder.py` | 209 行/4 测 | 三合一基座 |
| `test_run_worker.py` | 1749 行/54 测 | 按被测对象拆 4 块；L1171 best-effort 测试加注释 |
| `test_seed_initial_users.py` | 41 行/1 测（主审期间并行提交） | 保留：subprocess 全新进程验证 `scripts/seed_initial_users` 完整关系映射并到达 schema gate，不连库，是该入口唯一覆盖 |
| `test_scheduled_agent_service.py` | 327 行/16 测 | 保留；2 个恢复测参数化 |
| `test_schema_bootstrap.py` | 58 行/2 测 | 保留 |
| `test_session_app_scope.py` | 27 行/1 测 | 合并→session_thread_status |
| `test_session_config_snapshot.py` | 54 行/3 测 | 保留 |
| `test_session_history_images.py` | 163 行/3 测 | 保留；3 个中文测试名统一；与 queue_history 共享 fixture |
| `test_session_message_audits.py` | 241 行/2 测 | 保留；2 测参数化；L11 起 168 行长测试拆分 |
| `test_session_queue_history.py` | 284 行/3 测 | 保留 |
| `test_session_thread_status.py` | 223 行/8 测 | 合并目标 |
| `test_tool_message_audit_service.py` | 358 行/6 测 | 三合一→message_recorder |
| `test_tool_service.py` | 48 行/1 测 | 可并入同域聚合文件 |
| `test_turn_references.py` | 162 行/8 测 | 保留（与 integration 层互补的正面样板） |
| `test_upload_stream_lifecycle.py` | 53 行/1 测 | 合并→workspace_service |
| `test_viewer_filesystem_service.py` | 308 行/12 测 | 保留；预览测试可跨入口参数化，保留两个调用边界 |
| `test_workdir_service.py` | 204 行/6 测 | 保留 |
| `test_worker_health.py` | 69 行/3 测 | 保留；与 redis 版命名统一 |
| `test_workspace_project_visibility.py` | 104 行/4 测 | 保留 |
| `test_workspace_service.py` | 435 行/21 测 | 合并目标 |
| `skills/test_remote_install.py` | 407 行/15 测 | 保留 |
| `skills/test_skill_draft.py` | 154 行/7 测 | 吸收 skill_versions 与 service 的 draft 测 |
| `skills/test_skill_edit_service.py` | 430 行/15 测 | 吸收 service 的 edit 测 |
| `skills/test_skill_service.py` | 2103 行/63 测 | 拆 5 块+参数化（见第六节） |
| `skills/test_skill_versions.py` | 11 行/1 测 | 合并→skill_draft |

### 10.8 unit/routers（22）+ server（3）+ models（2）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_agent_management.py` | 126 行/2 测 | 保留；`_ListRepo` 自造签名加注释 |
| `test_api_key_security.py` | 252 行/7 测 | 保留；service/repository 部分拆出归位 |
| `test_auth_router_cli_auth.py` | 82 行/1 测 | 保留（CLI 授权唯一 HTTP 覆盖） |
| `test_chat_project_schema.py` | 16 行/2 测 | 合并→public_session_contract |
| `test_dashboard_superadmin_access.py` | 184 行/6 测 | 保留 |
| `test_evaluation_resource_permission.py` | 71 行/3 测 | 与 knowledge 版合并（真实判定 + fake 数据源，是健康范式） |
| `test_http_exception_passthrough.py` | 188 行/6 测 | 保留（横切守卫范本） |
| `test_knowledge_resource_permission.py` | 81 行/3 测 | 与 evaluation 版合并；脱敏测试拆出 |
| `test_knowledge_router_cleanup.py` | 765 行/18 测 | 更名；双胞胎参数化；2 个 job service 测迁 unit/services；33 处 management 模块 patch 收敛 |
| `test_knowledge_workspace_import.py` | 88 行/2 测 | 保留（grep 确认 integration 无对应，独占覆盖 workspace→KB 导入） |
| `test_mcp_router.py` | 231 行/9 测 | 保留；3 个 422 校验测合并为参数化（与 10.4 integration 版分层：本文件独有普通用户脱敏视角） |
| `test_mention_router.py` | 64 行/2 测 | （重写）或删除 |
| `test_public_agent_event_cursor.py` | 18 行/1 测 | 合并→public_session_contract |
| `test_public_agent_resume_schema.py` | 49 行/2 测 | 合并→public_session_contract |
| `test_public_knowledge_tools_errors.py` | 44 行/1 测 | 保留 500 分支；404 分支与 integration 去重 |
| `test_public_session_contract.py` | 154 行/7 测 | 合并目标（3 个微文件） |
| `test_scheduled_agent_router.py` | 106 行/3 测 | 保留；`/scheduled-agents` 墓碑集中 |
| `test_skill_router.py` | 408 行/14 测 | **重写**：12/14 测 mock 掉权限闸门（见 G 类）；保留 L397 正例模式 |
| `test_system_router.py` | 77 行/4 测 | 保留；discovery 断言与 integration 去重 |
| `test_upload_boundary.py` | 133 行/6 测 | 保留（分层正面样板） |
| `test_user_config_router.py` | 56 行/1 测 | 保留（真 sqlite 验证 current-user 隔离，健康范式） |
| `test_workspace_knowledge_tree.py` | 162 行/3 测 | 保留 |
| `server/test_auth_password_validation.py` | 52 行/3 测 | 保留 |
| `server/test_cors_config.py` | 70 行/5 测 | 保留 |
| `server/test_model_provider_router.py` | 67 行/4 测 | 保留 |
| `models/test_chat_invalid_tool_call_sanitize.py` | 176 行/7 测 | 保留；invoke/ainvoke/stream/astream 四测参数化；移除导入期 env 副作用 |
| `models/test_rerank.py` | 127 行/8 测 | 保留（参数化范本） |

### 10.9 unit/repositories（13）+ storage（10）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_agent_repository.py` | 387 行/20 测 | 合并目标；单值假参数化清理 |
| `test_agent_repository_delete_guard.py` | 196 行/7 测 | 合并→agent_repository |
| `test_agent_run_repository.py` | 1017 行/23 测 | 保留（跨层刻意）；分节；删 L735/737 冗余断言 |
| `test_agent_state_repository.py` | 46 行/2 测 | 归组保留 |
| `test_agent_turn_repository.py` | 232 行/2 测 | 保留 |
| `test_department_repository.py` | 73 行/1 测 | 保留 |
| `test_knowledge_base_repository_cache.py` | 186 行/5 测 | 保留；2 测参数化；标注 integration 对应物 |
| `test_knowledge_chunk_repository.py` | 54 行/1 测 | 归组保留；删重复装饰器 |
| `test_session_memory_history.py` | 256 行/4 测 | 合并→storage/test_session_repository；近同构测试去重 |
| `test_session_workdir.py` | 91 行/6 测 | 删 2 个显式迁移守卫；残余合并→session_repository |
| `test_skill_repository.py` | 31 行/1 测 | 归组保留 |
| `test_tool_message_audit_repository.py` | 108 行/4 测 | 保留 |
| `test_user_repository.py` | 143 行/3 测 | 保留；过载测试拆分 |
| `test_agent_run_timing.py` | 76 行/3 测 | 归位 unit/models/ |
| `test_langgraph_checkpointer_setup.py` | 142 行/4 测 | 保留 |
| `test_minio_bounded_download.py` | 66 行/2 测 | 合并目标（release 语义） |
| `test_minio_bucket_creation.py` | 81 行/4 测 | 保留 |
| `test_minio_image_content.py` | 51 行/2 测 | 保留 |
| `test_minio_public_images.py` | 136 行/5 测 | 保留；release 2 测迁出 |
| `test_neo4j_manager.py` | 31 行/3 测 | 归组保留 |
| `test_postgres_manager_schema.py` | 56 行/3 测 | 保留 |
| `test_redis_manager.py` | 169 行/9 测 | 保留 |
| `test_session_repository.py` | 665 行/12 测 | 合并目标（SessionRepository 归一） |

### 10.10 unit/agents（18）+ middlewares（10）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `callbacks/test_model_request_timing.py` | 36 行 | 保留 |
| `skills/test_mysql_reporter_scripts.py` | 164 行 | 保留 |
| `skills/test_skill_runtime.py` | 367 行 | 保留 |
| `test_agent_stream_close.py` | 100 行 | 保留 |
| `test_base_agent_checkpointer.py` | 25 行/1 测 | 归组保留（与 integration checkpoint_concurrency 为一对） |
| `test_base_tool_event_normalize.py` | 168 行 | 保留 |
| `test_builtin_discovery.py` | 239 行 | 保留；slug oracle 择一 |
| `test_chatbot_prompt.py` | 23 行 | 归组保留 |
| `test_checkpoint_cleanup.py` | 193 行 | 保留 |
| `test_context_auth.py` | 658 行 | 保留；L316 起 151 行长测试拆分 |
| `test_network_retry.py` | 328 行 | 保留 |
| `test_provider_reasoning.py` | 252 行 | 保留 |
| `test_streaming_toolcall_fix.py` | 110 行 | 保留 |
| `test_summary_graph_config.py` | 255 行 | 保留 |
| `test_thread_event_routing.py` | 49 行 | 保留 |
| `test_tool_approval.py` | 92 行 | 保留 |
| `test_web_search_provider.py` | 85 行 | 保留 |
| `toolkits/buildin/test_ask_user_question.py` | 222 行 | 保留 |
| `test_cooperation_middleware.py` | 133 行 | 保留 |
| `test_memory_middleware.py` | 41 行 | 保留 |
| `test_model_input_middleware.py` | 223 行 | 保留 |
| `test_runtime_authorization.py` | 186 行 | 保留 |
| `test_skills_middleware.py` | 627 行 | 保留 |
| `test_steer_middleware.py` | 97 行 | 保留（可选吸收 safety_gate） |
| `test_steer_safety_gate.py` | 181 行 | 保留 |
| `test_summary_middleware.py` | 1155 行 | 保留；内部参数化（实证对见第六节） |
| `test_token_usage_middleware.py` | 415 行 | 保留 |
| `test_tool_error_guard.py` | 321 行 | 保留 |

### 10.11 unit/config、utils、toolkits、workspace 及其余

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `config/test_docker_compose_checkpointer.py` | 153 行/10 测 | 保留；循环改参数化 |
| `config/test_docker_compose_service_boundaries.py` | 371 行/22 测 | 保留（config 域范本） |
| `config/test_docker_compose_worktree_slots.py` | 150 行/4 测 | 保留 |
| `config/test_nginx_static_assets.py` | 34 行/2 测 | 可并入部署契约文件 |
| `config/test_options.py` | 158 行/8 测 | 保留 |
| `config/test_preview_owner_boundaries.py` | 78 行/2 测 | 保留 |
| `config/test_runtime_capacity.py` | 23 行/3 测 | 合并→runtime_settings |
| `config/test_runtime_directory.py` | 48 行/4 测 | 合并目标 |
| `config/test_user_config.py` | 70 行/3 测 | 保留 |
| `utils/test_datetime_utils.py` | 25 行/3 测 | 归组保留 |
| `utils/test_fd_io.py` | 127 行/7 测 | 合并→infrastructure/test_filesystem |
| `utils/test_filepreview.py` | 70 行/4 测 | 保留 |
| `utils/test_image_processor.py` | 62 行/3 测 | 保留 |
| `utils/test_lifespan.py` | 106 行/4 测 | 保留 |
| `utils/test_mime_consumers.py` | 54 行/2 测 | 保留 |
| `utils/test_office_conversion.py` | 102 行/5 测 | 保留 |
| `utils/test_paths.py` | 75 行/5 测 | 合并目标；消除撞名 |
| `utils/test_shared_files.py` | 72 行/4 测 | 保留 |
| `utils/test_string_utils.py` | 28 行/3 测 | 归组保留 |
| `toolkits/test_install_skill.py` | 204 行/6 测 | 保留 |
| `toolkits/test_kbs_tools.py` | 905 行/27 测 | 保留；三件套 fixture + wrapper 合一 |
| `toolkits/test_ocr_parse_file_tool.py` | 312 行/7 测 | 保留；2 测参数化；取消骨架 helper 与 parser_facade 共享 |
| `toolkits/test_tool_registry.py` | 18 行/1 测 | 归组保留 |
| `workspace/test_fd_copy_consumers.py` | 80 行/3 测 | 保留 |
| `workspace/test_filesystem.py` | 308 行/13 测 | 保留；2 测参数化 |
| `workspace/test_managed_workdir_paths.py` | 71 行/4 测 | 保留 |
| `workspace/test_workdir.py` | 236 行/10 测 | 保留；取消三测收敛 |
| `graphs/test_milvus_graph_build.py` | 1375 行/42 测 | 拆 4 文件 |
| `permissions/test_resource_permission.py` | 252 行/20 测 | 保留 |
| `backends/test_semantic_chunking.py` | 46 行/3 测 | 归位 unit/knowledge/ |

### 10.12 unit/knowledge（20）+ eval（6）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `test_chunk_config_validation.py` | 45 行/4 测 | 合并→plugins/test_ragflow_like_chunking |
| `test_evaluation_task_cancellation.py` | 121 行/2 测 | 归位 unit/knowledge/eval/ |
| `test_file_exists.py` | 100 行/1 测 | 与文件操作族整合 |
| `test_file_listing_scaling.py` | 424 行/10 测 | 更名；与 manager 族整合 |
| `test_file_size_fallback.py` | 167 行/6 测 | 保留；stat_file 转发断言精简 |
| `test_kb_utils.py` | 72 行/7 测 | 保留 |
| `test_knowledge_base_update.py` | 256 行/2 测 | 与 manager 族整合；删恒真断言与 `make_kb` 死 helper（L44） |
| `test_knowledge_cache.py` | 118 行/4 测 | 保留 |
| `test_knowledge_manager_ownership.py` | 145 行/6 测 | 更名；与 manager 族整合 |
| `test_knowledge_runtime_cache.py` | 248 行/5 测 | 保留；删恒真断言 |
| `test_lazy_file_metadata.py` | 116 行/2 测 | 保留；删恒真断言 |
| `test_milvus_retrieval_config.py` | 51 行/3 测 | 合并→plugins/test_milvus_kb |
| `test_milvus_text_search.py` | 189 行/9 测 | 保留；后 3 测可并入 test_milvus_kb |
| `test_office_pdf_preview.py` | 232 行/7 测 | 保留；30MB 文案与 integration 侧共享常量 |
| `test_paddleocr_api_parser.py` | 302 行/9 测 | 保留；三个 model-specific payload 测参数化 |
| `test_parser_capabilities.py` | 144 行/7 测 | 保留；zip 弱子集的返回形状断言并入 facade 后删除 |
| `test_parser_facade.py` | 561 行/28 测 | 拆 3 份；清理死 skip 与 `_build_docx`/`_build_png` 死 helper |
| `test_runtime_initialization.py` | 84 行/3 测 | 与 manager 族整合 |
| `test_sample_question_utils.py` | 157 行/6 测 | 保留 |
| `test_url_fetcher.py` | 215 行/15 测 | 保留（SSRF 契约范本）；8 个 `test_backend_*` 参数化 |
| `eval/test_benchmark_generation.py` | 515 行/17 测 | 保留；autouse fixture 上提；移除导入期 env 副作用 |
| `eval/test_dataset_generation_resume.py` | 540 行/12 测 | 保留 |
| `eval/test_evaluator.py` | 67 行/4 测 | 保留；移除导入期 env 副作用 |
| `eval/test_metrics.py` | 48 行/4 测 | 保留；移除导入期 env 副作用 |
| `eval/test_result_filters.py` | 110 行/3 测 | 保留 |
| `eval/test_service_generation.py` | 254 行/7 测 | 保留 |

### 10.13 unit/sandbox（4）+ plugins（5）+ performance（6）

| 文件 | 规模 | 判定 |
| --- | --- | --- |
| `sandbox/test_sandbox_backends.py` | 1829 行/77 测 | 保留；内部大整改（见第六节） |
| `sandbox/test_sandbox_download.py` | 191 行/10 测 | 保留；FakeBackend×10 收敛 + 用例参数化；见 N10 覆盖不对称 |
| `sandbox/test_sandbox_provisioner_client.py` | 147 行/7 测 | 保留 |
| `sandbox/test_sandbox_provisioner_config.py` | 1824 行/61 测 | 保留；内部大整改 |
| `plugins/test_chunk_source_lines.py` | 126 行/14 测 | 保留 |
| `plugins/test_dify_kb.py` | 219 行/7 测 | 保留 |
| `plugins/test_milvus_kb.py` | 1228 行/32 测 | 合并目标；3 个重试测参数化；`make_file_record` 与 knowledge 侧共享 |
| `plugins/test_notion_kb.py` | 223 行/8 测 | 保留 |
| `plugins/test_ragflow_like_chunking.py` | 369 行/18 测 | 合并目标；末尾 kb_utils 4 测迁出 |
| `performance/test_analysis.py` | 233 行/10 测 | 保留；重复组 oracle 择一 |
| `performance/test_cli.py` | 146 行/6 测 | 保留；subprocess/--help 与 importlib 冒烟降级 |
| `performance/test_load.py` | 386 行/21 测 | 保留 |
| `performance/test_matrix.py` | 534 行/19 测 | 保留 |
| `performance/test_report.py` | 85 行/5 测 | 保留 |
| `performance/test_stage_probe.py` | 86 行/3 测 | 保留；时序断言标注 flaky 风险 |

## 十一、复核记录（v2）

主报告为一次性生成；随后执行了完整复核轮与两轮启发式发现，本节记录裁决统计与已套用的修正。**本版正文已全部按复核结果修正**，此处保留修正台账以便追溯。

### 11.1 复核方法与裁决统计

复核由 5 个独立子智能体执行（不继承主报告写作过程），逐条读取涉及文件并给出行号级证据，共裁决 91 项主张：

| 复核批次 | 覆盖 | CONFIRMED | PARTIAL | REFUTED |
| --- | --- | --- | --- | --- |
| 4.1 #1-8 | 真重复前段 | 7 | 1 | 0 |
| 4.1 #9-15 + 4.2 #1-6 | 真重复后段 + 遗留前段 | 9 | 4 | 0 |
| 4.2 #7-11 + 合并 #1-15 | 遗留后段 + 合并前半 | 15 | 5 | 0 |
| 合并 #16-28 + 脚手架 #1-10 | 合并后半 + 脚手架 | 11 | 12 | 0 |
| 拆分/命名 27 项 | 第六节 + 第八节 | 20 | 7 | 0 |
| **合计** | **91 项主张** | **62** | **29** | **0** |

合计：62 项完全确认、29 项细节偏差（全部已修正）、**0 项被整体推翻**；唯一的方向性改正是 4.2#12（`BACKGROUND_JOB_GATE_ID` 存在 CI 编排入口，从"存疑待核实"改判"有意设计，保留"，计入上表 PARTIAL）。结构面与语义面两轮发现型探索另产出 13 组新发现（第十二节），其中"3 个子目录漏审"导致补审 8 个文件；终审校对另发现 4 个文件未入索引（2 个为主审期间并行提交、2 个为主审已读但索引遗漏），均已补齐。

### 11.2 已套用的重要修正

- 数量类：no-op fixture 14→55 处空定义；手写 sqlite 引擎约 10→21 文件；`delete_created_threads` 导入方 2→4；apikey 样板约 10→12 文件；KB seed 约 8→10 次；kbs_tools 三件套约 15→严格 10/宽松 19；zip 族 6→严格 5（+md 变体）；`FakeKnowledgeBase` 4+1→逐字 3 份（file_size_fallback 是局部 TestKB、milvus_kb 无此存根）。
- 行号类：`fk_messages_run_turn_session` 498→492；print 117/131→119/133；冗余断言 731/734→735/737；state view 三测 1073/1099/1176→1077/1104/1180；replay 跨层 import L54-63→L20-22；extended helper L411-504→L415-512。
- 定性类：`test_parser_capabilities` zip 测试是唯一直接断言返回形状的测试（动作从"删"改"并入断言后删"）；"重复组拒绝"三现但"保留旧输出"仅两现；`test_session_workdir` 是 2 个显式守卫 + 2 个迁移期副产品；`_load_compose` 仅 2 处复制；extended 文件 5 个 helper 中 2 个在母文件无对应；marker 双注册的描述文案部分不一致；`test_provider_reasoning_live.py` 不并入 provider 探针（门禁与 helper 均不同）；`test_workdir_user_workspace` 合并理由弱化为同域模块；`login_rate_limit:95/109` 判为正控断言非弱断言；conftest 三个"疑似死 fixture"均 autouse，排除。
- 一致性：第八节 `unit/test_paths.py` 笔误更正为 `unit/utils/test_paths.py`；`test_agent_lifecycle_e2e.py` 1577→1634 行、`test_run_worker.py` 1747→1749、`test_chat_service_sync.py` 1310→1313（工作树并行演进所致）。

### 11.3 快照漂移说明

审计期间工作树在并行演进：staged 新增 `backend/test/{e2e/integration/api/integration/services}` 下 3 个 `test_agent_feedback*.py` 与 `backend/yuxi/.../feedback.py` 路由，另有 `test_schema_migration_version.py`、`run_tests.sh`、`system-tests.yml`、`support/openai_replay_server.py` 的未提交修改，且主干落入两个 fix 提交。主审快照约 336 个测试文件，复核时点实测 339（含 staged）。所有行号以复核时点磁盘状态为准。执行清理前应重新校对涉及文件的行号。另有两类已知漂移：staged 未提交修改（如 `support/openai_replay_server.py` 299→351 行）与 ±1 行级漂移（若干文件在审计日凌晨提交，10.x 表中个别行数为快照值）。测试函数计数按 `def` 统计，与 `pytest --collect-only` 收集数可有 ±1 出入。

## 十二、复核轮新发现

以下问题在主审中未覆盖，均由发现型子智能体给出行号级证据后收录。

### N1. CI 与门控的零接线（优先级最高的新发现）

- CI 现状：`test.yml:65` 只跑 `pytest test/unit -m 'not slow'`；`system-tests.yml` 是逐文件白名单编排（无整目录步骤）；全 workflows **无任何一处引用下述门控环境变量**。
- 双零覆盖（默认不跑 + CI 不跑，且无其它层兜底）：
  1. **BUILTIN_PROVIDERS 目录事实**（每个内置 provider 的 base_url/api_key_env/模型 id）：唯一验证者是 `RUN_MODEL_PROVIDER_CONNECTIVITY` 门控的 connectivity 套件；`unit/services/test_model_provider_service.py:462-465` 只断言 schema 与"ollama 不在内"。siliconflow 改名模型时无任何信号。
  2. **沙箱镜像 grep 契约**（容器内 grep + NUL 传输协议，原决策已归并至[沙盒环境决策](../decisions/implemented/2026-10-15-sandbox-environment.md#后端装配与旧执行路径的证据边界)，此处保留审计时的旧后端事实）：唯一测试 `integration/sandbox/test_sandbox_native_grep.py` 由 `TEST_SANDBOX_URL` 门控，且该变量在全仓库无任何配置来源（yml/sh/compose/toml 零命中）；unit 侧 grep 族 mock 掉 `_grep_root`。
- 其余门控（`YUXI_REASONING_*`、`E2E_VISION_MODEL`/`E2E_NON_VISION_MODEL`、`REFERENCE_PROBE_MODEL`）有默认单测兜底（如 `test_provider_reasoning.py` 解析族、`test_model_input_middleware.py` OCR fallback 族），风险可接受，但应在 CI 显式接入门控（哪怕单 provider）或文档化运行频率。
- 运行时条件 skip：`test_chat_router.py:698/702/729/733`、`test_viewer_filesystem_security.py:21`、`e2e_helpers.py:27`（429 配额即跳过，真实模型断言可被静默吞掉）——应改为显式报告。
- CI 白名单缺口：`integration/graphs/` 两文件、`integration/sandbox/test_sandbox_native_grep.py`、`integration/api/test_debug_message_projection.py` 不在 system-tests.yml，仅 `run_tests.sh integration` 整目录能带到。
- marker 筛选缺口（已实测）：真正会被 `-m integration` 漏掉的是 `test_provider_reasoning_live.py`（全文零标记）与 `test_sandbox_native_grep.py`（仅 asyncio）；graphs/identity 两目录及 `test_graph_build_status.py`、`test_personal_skill_router.py` 为逐测试标记（等效但与文件级 pytestmark 惯例不一致）。

### N2. import 期环境副作用（8 个文件）

`os.environ.setdefault("OPENAI_API_KEY", ...)` 在收集期即污染测试进程：`eval/test_evaluator.py:5`、`eval/test_metrics.py:5`、`eval/test_benchmark_generation.py:9`、`test_model_provider_service.py:6`（"test-key"）、`test_oidc_service.py:11`（值不一致，用 "dummy"）、`models/test_chat_invalid_tool_call_sanitize.py:19`、`test_attachment_service.py:12` + **L13 额外直写 `YUXI_RUNTIME_DIR`**（源码 `runtime_settings.py:22` 直接读取，影响路径解析类测试的进程级前置）。全部改为 fixture 内 `monkeypatch.setenv`（正例：`test_package_import.py:5`）。

### N3. patch 依赖 re-export 别名链

- `ModelCache` 单例（定义 `models/providers/cache.py:198`）经 `models/chat.py:19`、`embed.py:10`、`rerank.py`、`agents/services/input_config` 四条 re-export 别名链被 4 个测试文件 20 处 patch；仅 `test_knowledge_base_update.py:182` 用规范路径。chat.py 清理 re-export import 即同时打断 4 个文件。统一 patch `yuxi.modules.models.providers.cache.model_cache`。
- skills 域 `__init__` re-export（`shared` 4 文件 36 处、`projection` 4 文件 12 处等）构成 patch 身份双重性；`knowledge management` 模块被 5 文件 71 处 patch；`pg_manager` 被 integration/services 7 文件 + `test_install_skill.py` patch——均应提 conftest fixture。
- 附带发现：`unit/performance/` 六文件用 unittest 风格断言（基于 AST 的断言审计会漏计）。

### N4. oracle 文案复制族（改一处源码文案多点崩）

| 文案 | 断言位置 |
| --- | --- |
| `"lease owner"` | 2 文件 7 处（见 4.1#4） |
| `"Memory 已关闭"` | unit 与 integration memory 各 1（跨层双写） |
| `"登录被锁定"` | test_auth_router + test_password_login_boundary |
| `"文件过大，当前仅支持 30 MB 以内的文件预览"` | test_file_preview_limits + test_office_pdf_preview（业务数字 30 MB 双耦合） |
| `"评估数据集生成任务已恢复"` | eval + integration/api resume 各 1 |
| `"只支持检索"` | test_kbs_tools + test_knowledge_router |
| `"任务已取消"` | background_job_repository + background_jobs_router |
| `"不可安装"` | skill_draft + skill_service（见 4.1#8） |

处置：优先断言异常类型/错误码；确需文案的提取共享常量模块。

### N5. 常量硬编码（三方平行定义）

- `"siliconflow-cn:Pro/BAAI/bge-m3"`：5 个文件硬编码（含 `integration/conftest.py:272` 公共 fixture 与 4 个 integration/api 测试文件），与源码默认 `options.py:109`、内置目录 `providers/builtin.py:208` 互不引用。`manager.py:515-522` 创建 KB 强校验该 spec 在 model_cache 中——内置目录改名即 30+ 用例连锁失败。测试应 import 源码共享常量。
- magic UID `"11111111-1111-4111-8111-111111111111"`：28 个测试文件 + 1 个共享 fixture 模块（`unit/agent_context_fixtures.py`）逐字复制，应入 `test/support/`。
- MinIO 逻辑桶名 `"knowledgebases"` 在 `test_office_pdf_preview.py:47`、`test_file_size_fallback.py:21` 与 `infrastructure/minio/client.py:67-69` 平行；JWT issuer 前缀 `"yuxi-know:"` 在 `test_auth_utils.py` 四处。

### N6. 跨测试模块 import 网（合并/删除前必须先解开的依赖）

- integration/services 四文件链：`test_agent_input_concurrency`（_create_schema/_drop_schema 定义方）← `test_agent_input_schema:14` ← `test_thread_priority_inputs:13-14` ← `test_input_attachments:12`（`SCOPE/_start/sessions` 以 noqa F401 转发再 re-export）。
- integration→unit：`test_thread_priority_inputs.py:603` import `test.unit.services.test_openai_event_adapter.native`。
- unit→unit：`test_milvus_text_search.py:10` import `test.unit.plugins.test_milvus_kb` 的 4 个假对象；`test_chat_service_langfuse_stream.py:150` import `test.unit.middlewares.test_steer_safety_gate._FinalAnswerModel`。
- e2e fixture 跨文件：`test_agent_bound_skill_e2e.py:24` import `test_permission_revocation_e2e.remote_tool`。
- sys.path 裸 import 三处：`test_database_access_paths.py:8`、`test_knowledge_projection_integrity.py:15`、`test_database_relation_integrity.py:13` 均 `from test_schema_migration_version import ...`。
- 隐性共享 helper：`unit/agent_context_fixtures.py`（无 `test_` 前缀的非测试模块）被 3 文件共用（chat_service_sync:13、langfuse_stream:15、run_worker:385/1035）。

### N7. e2e 稳定性面

- **同一文件混用两种 replay host**：`test_agent_lifecycle_e2e.py` 的 L351/631/769/1046/1138 用 `http://api:8765`、L1182/1237/1576/1617/1622 用 `http://localhost:8765`——当前 CI 在 api 容器中启动 replay 并执行 E2E，两种地址均指向该容器，可以到达。裸机运行需要明确 host 映射，可收敛为单一配置以减少硬编码。
- timeout 缺失 9 个 e2e 文件（agent_async、agent_call_entrypoints、personal_skill、ocr_config、document_parsing、provider_reasoning、read_file_multimodal、milvus_text_retrieval，以及仅裸 e2e 标记的 test_mcp_stdio_security.py）；slow 缺失 3 个（ocr_config、document_parsing、milvus_text_retrieval）；已有 timeout 的 12 个文件取值 120/180/240/360 混用。统一 `[asyncio, e2e, slow, timeout(N)]` 基线。

### N8. 长测试函数（AST 实测 23 个 >150 行）

最影响失败归因的：`integration/api/test_chat_router.py:60`（**287 行**，串 6+ 独立契约）、`e2e/test_agent_bound_skill_e2e.py:35`（238）、`e2e/test_session_cooperation_e2e.py:34`（218）、`e2e/test_agent_lifecycle_e2e.py:306`（234，FIFO）、`integration/api/test_agent_config_resource_authorization.py:168`（199）、`integration/services/test_user_skill_projection.py:48`（221）。完整清单见批次记录。处置为共享 fixture 后按契约段拆分。

### N9. 死 helper 补充（5 处，均不在初稿清单）

`integration/api/test_project_api.py:110 _create_lifecycle_session`；`unit/knowledge/test_knowledge_base_update.py:44 make_kb`；`unit/knowledge/test_parser_facade.py:163 _build_docx` 与 `:189 _build_png`；`unit/services/test_attachment_service.py:163 _write_local_parsed_object`。全部定义后零引用，直接删。

### N10. 覆盖不对称风险（"消费侧 stub 即契约"）

`unit/sandbox/test_sandbox_download.py`（10 测）是 `download_sandbox_directory` 防护逻辑（越界/1000 文件/64 层/100MB/部分失败清理）的**唯一覆盖层**。消费方 `skills/remote.py:210`、`personal.py:282` 在 unit 层被整体 mock，且 FakeBackend 自造的 `ls` 条目形状 `{"path","is_dir","size"}` 与真实构造 `backend.py:611` 的 `FileInfo = {"path", "is_dir"}` **无任何层交叉验证**。建议给 backend `ls` 条目形状补一条真实/准真实断言。同类：audits 投影散落三个文件（见第五节 #31）。

### N11. 参数化遗漏补充（第六节清单之外）

`models/test_chat_invalid_tool_call_sanitize.py:144-176`（invoke/ainvoke/stream/astream 四测同模板）、`unit/knowledge/test_paddleocr_api_parser.py:72/117/157`（三胞胎）、`unit/sandbox/test_sandbox_download.py:37-175`（七测同脚手架）、`unit/knowledge/test_url_fetcher.py:116-190`（八测同脚手架）、`unit/services/test_model_provider_service.py:103/131/159 与 :356-521`（拒绝族）。

### N12. 中文测试函数全清单

全库 14 个，集中在 2 个文件：`unit/services/test_agent_input_messages.py`（11 个）与 `test_session_history_images.py`（3 个：L85/L113/L138）。统一为英文命名（与全库风格一致）。

### N13. 健康范式确认（复核轮反向价值）

复核与探索同时确认了一批"看似可疑实则健康"的样本，供清理时作为对照：`test_upload_boundary.py` 的 AST 架构守卫、`test_http_exception_passthrough.py` 的 `caught.value is error`、`test_user_config_router.py`（真 sqlite 验证 current-user 隔离）、`test_evaluation_resource_permission.py`（真实执行权限判定）、monkeypatch 全局单例全部经 pytest 自动恢复（`pg_manager.pool = ...` 直接赋值全库零命中）、约 400 条路径字面量对路由表全量比对**零死引用**（符号级约 1500 处 import/setattr 目标同样零死引用）。

## 附：方法与证据说明

- 主审：13 个独立批次逐文件精读，要求测试函数名/断言/import 级证据，过期引用在 `backend/yuxi/` 下 grep 验证。
- 复核轮：5 个独立子智能体逐条复核 91 项主张（62 确认 / 29 细节修正 / 0 推翻，其中 1 项方向性改判），修正台账见 11.2。
- 发现轮：结构面（重复 helper、import 期副作用、跨测试 import 网、中文测试名、文案复制族、硬编码、marker 一致性、弱断言、参数化遗漏）与语义面（路径字面量对路由表全量比对、monkeypatch 目标静态解析、门控清单与 CI 接线核查、长测试函数 AST 统计、命名映射抽查）两轮启发式，误报项（conftest 死 fixture、`assert True`、conftest print、日期硬编码）已核为误报并排除。
- 补审：3 个子目录（integration/graphs/identity/sandbox）4 个漏审文件 + 审计期间 staged 的 4 个新文件，共 8 个文件补齐精读。终审校对另发现 4 个文件未入索引（`e2e/test_input_projection_recovery_e2e.py`、`unit/services/test_seed_initial_users.py` 为主审期间并行提交，`unit/routers/test_knowledge_workspace_import.py`、`unit/routers/test_mcp_router.py` 为主审已读但索引遗漏），前两者补读后连同后两者均已补入第十节。
- 未执行任何测试运行；行号以复核时点磁盘状态为准（快照漂移见 11.3）。执行清理前按改动面选择最低证据（见根 [AGENTS.md](https://github.com/xerrors/Yuxi/blob/main/AGENTS.md) 证据规则表）：纯移动与合并补相关 unit + 受影响 integration；涉及 fixture 上提的批次补真实 HTTP integration。
