# Milvus 3 全新部署与文本检索

状态：implemented
类型：feature
Owner：backend/yuxi/modules/knowledge/implementations/milvus.py

## 问题

知识库需要在全新 Milvus 3 数据库中完成向量、BM25 和混合检索，并提供词项约束、短语约束及命中高亮。部署镜像与 SDK 版本需要一致；BM25 和融合分数需要显示各自的原始数值。用户明确允许重置全部数据，代码与数据库无需兼容旧版本。

## 决策

### 实现方案

开发与生产 Compose 固定使用 Milvus/PyMilvus 3.0.2、etcd 3.5.25。Milvus 使用内嵌 Woodpecker 的对象存储模式；MinIO 二进制固定到 RELEASE.2024-12-18T13-15-44Z，并校验上游 SHA256，以满足[条件写入要求](https://milvus.io/docs/woodpecker.md#object-storage-compatibility-for-storagetypeminio)。启动前的 `storage-init` 为 Milvus 绑定目录设置 UID/GID 999，失败阻止 Milvus 启动；统一初始化与镜像权限由[存储权限决策](./2026-10-17-container-storage-permissions.md)拥有。PostgreSQL 继续拥有知识库、文件和 chunk 事实，MinIO 保存原件及解析产物，Milvus 保存派生索引。

知识库与图向量 executor 通过各自连接 alias 明确创建并使用 `MILVUS_DB`，默认 `yuxi`；连接或集合加载失败显式报错。集合按当前 schema 创建，模型、维度或文本 schema 不匹配时拒绝使用，查询入口不删除集合。chunk 正文开启中文 analyzer 与 match，BM25 由服务端生成。

检索配置提供必含词、排除词、完整短语及高亮开关。文本条件与文件名过滤组成同一个条件，应用到向量与 BM25 通道；图检索候选在融合前也受同一条件约束。文本作为 JSON 字符串字面值进入表达式，非法类型和检索模式显式失败。检索测试以 `400` 表达参数错误，以 `500` 表达执行失败。

服务端高亮转换为结构化文字段，前端安全渲染，正文保持完整。3.0.2 的高亮只支持 `TextMatch` 来源，短语是否符合词序由 `PHRASE_MATCH` 决定。SDK 的混合检索不返回高亮，因此在已召回的 ID 内补一次向量检索获取服务端词项高亮，保留原融合排序与分数。纯向量检索只高亮正向文本条件；关键词与混合检索还高亮查询词。公共检索的输出投影在 `metadata` 中保留高亮与评分类型。

检索测试页面直接选择三种模式、设置文本条件；保存默认值仍使用现有配置入口。界面区分余弦、BM25、混合及图谱融合分数，零分仍可见。余弦阈值只作用于纯向量检索。参数和使用方法分别由[查询 API](../../../advanced/knowledge-base-api.md#milvus-文本检索参数)、[知识库教程](../../../intro/knowledge-base.md#_3-验证检索)拥有；全新部署步骤由[部署参考](../../../advanced/deployment.md#milvus-3-全新部署)拥有。

第一阶段保持当前密集向量与 BM25 索引算法。Storage V3、TEXT、聚合、近重复检测和多模态模型需要独立消费场景及质量证据，不进入本次实现。

## 替代方案

- 只替换镜像：部署版本变化，但文本约束、高亮及评分语义仍缺少实现与证据。
- 全量改写到 MilvusClient：现有 ORM 支持固定的 3.0.2 协议，当前迁移成本较高；继续升级 SDK 时需要重新验证弃用 API。
- 保留旧数据迁移和 schema 兼容：用户已明确放弃旧数据及兼容要求。
- Woodpecker 使用本地 WAL：可以沿用旧 MinIO，但需要另一个持久存储配置。本决定沿用共享对象存储并更新其必要协议能力。

## 后果

部署以空的应用状态目录开始。PostgreSQL、对象存储及索引需一致重置，重新初始化账号、模型和知识库；不提供旧数据原地升级承诺。高亮启用时，混合检索增加一次只读 Milvus 搜索；关闭高亮可省去这次请求。过滤和高亮按词项而非字面子串工作，短语按分词后的连续词序匹配。排序和阈值变化可能改变既有查询结果，需要业务语料评估。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 镜像与 SDK 使用 3.0.2，能创建、写入及读取集合 | 只修改版本声明、存储不可写、WAL 不可用 | Compose、依赖锁、MinIO Dockerfile | 实例版本回读，真实 worker 索引，PG/Milvus 正文回读；重启后回读五个片段 | 首次目录权限失败、旧 MinIO 条件写入失败由真实探针暴露，当前拓扑验证通过 | Passed |
| 数据库作用域明确，失败不回落或删集合 | alias 错误、模型或 schema 不匹配 | 两个 Milvus executor | 非默认数据库探针，图实体/关系集合真实写入与查询；相关 unit | 数据库切换、集合加载失败；模型、维度、缺失字段和文本 schema 不匹配 | Passed |
| 三种模式与文本过滤返回正确 chunk | 通道漏过滤、表达式注入、错误词序 | MilvusKB.aquery、HTTP 路由 | 真实 HTTP / worker E2E，固定向量使文本筛选具有独立 oracle | 排除词、逆序短语、中文与特殊字符；非法模式/参数返回 400；图候选绕过条件 unit | Passed |
| 高亮完整、安全，公共结果保留评分类型 | 丢正文、HTML 注入、补取高亮改变排名、公共投影丢字段 | executor、公共输出投影、结果组件 | 三模式 query-test 与 external.retrieve 回读，实际组件 SSR，浏览器 DOM 与截图 | HTML 文本、零分、BM25 大于 1；高亮搜索顺序不同但融合分数和顺序保持 | Passed |
| 重索引与删除保持派生数据一致 | 重复 chunk、残留实体/关系集合 | worker、PG repository、Milvus executor | 重索引后 PG/Milvus 均五个片段；删除文件、知识库后回读 PG 与集合不存在 | 第二次索引、删除目标文件与整个知识库 | Passed |

实际执行命令与范围：

- `docker compose exec -T -u 0 api uv run --group test pytest test/unit -m "not slow" -q`：2445 passed、55 skipped。容器没有挂载根 Compose/脚本，相关配置测试显式跳过；补跑 `docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo/backend api uv run --no-sync --no-dev pytest test/unit/config -q`：88 passed、无 skipped。测试环境的 editable metadata 权限使同步命令需要以容器 root 执行。
- `docker compose exec -T -e E2E_USERNAME -e E2E_PASSWORD api uv run --no-sync --group test pytest test/e2e/test_milvus_text_retrieval_e2e.py -q --tb=short --disable-warnings`：1 passed。真实 API、ARQ worker、PostgreSQL、MinIO、Milvus 3.0.2；嵌入模型用固定四维向量的本地 HTTP 协议重放，不依赖外部模型。运行 workflow 启动并等待 Milvus，失败会拒绝该 gate。
- `docker compose exec -T frontend pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`：Passed，前端完整 unit 406 passed。实际 Vue 组件覆盖原始评分、零分、HTML 文本与完整正文；最终片段组件复跑 6 passed。
- Playwright 真实页面验证：关键词/混合模式、命中来源、完整正文、浅/深色、390px 窄屏、空结果、加载和真实 HTTP 400 的错误投影 Passed；截图保留为本地交付证据。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`cd docs && pnpm run build`、`git diff --check`：Passed。独立 Review：Passed。

生产 Compose 完成配置解析，生产拓扑、ARM64 镜像、冷 GitHub runner 及真实图谱融合后的文本筛选 Not run；图候选约束由 unit 覆盖，图向量写入、查询和删除由真实 E2E 覆盖。固定向量证明协议和筛选正确性，不替代真实嵌入/重排模型的语料质量评估。
