# 知识库缺少向量模型时显示可处理的错误

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/services/background_jobs.py

## 问题

历史 Milvus 知识库可能没有 Embedding 模型配置。文档解析成功后，入库在创建向量集合时失败，但后台任务只显示通用错误，详情页也没有配置缺失提示。

## 决策

### 实现方案

知识库执行器把已有的缺少模型校验表达为固定文案的领域异常，并保留其类型。知识库任务服务仅对该异常生成明确的 `public_error`，随既有 JSON 结果持久化；后台任务观察投影只读取这个刻意公开的字段，任意底层异常继续隐藏。任务和文件的状态、权限及发布边界保持现有语义。

知识库详情页根据当前加载的 Milvus 配置显示常驻提醒，说明影响及联系管理员处理；列表卡片使用同一判断显示缺少模型标签。其他连接器不依赖本地 Embedding，不显示此提醒。现有界面不支持改写已建库的向量模型，因此不提供无效的配置跳转。不修复历史配置，不重试用户任务，也不追溯改写历史失败记录。

## 替代方案

- 直接显示所有异常：底层异常可能含凭据、对象路径或上游响应，不采用。
- 只在前端提示：无法解释后续新任务失败，不采用。
- 自动选择默认模型：会改变知识库语义和外部副作用，不属于本需求。

## 后果

`public_error` 是服务端生产者明确承担脱敏责任的公开字段，不能直接赋值原始异常。历史任务没有此字段，继续使用通用提示。页面提醒只判断是否缺少模型配置，不证明模型服务本身可用。失败发生在文件领取前时，文件继续保持已解析状态，不伪造成功入库。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新任务显示缺少模型原因 | 两次摘要投影丢失原因 | 知识库任务服务、后台作业观察投影 | 后台任务 unit 47 项；真实 HTTP/worker 后回读 PostgreSQL | 缺少模型配置的已解析文件入库失败且提示明确，未创建分块 | Passed |
| 普通异常仍隐藏细节 | 原始异常被直接公开 | 后台作业观察投影 | unit；既有 HTTP 观察隐私用例 | 含秘密的原始错误不能出现在响应 | Passed |
| 详情页提示与当前配置一致 | 正常库或连接器误报、切库残留 | 知识库详情页 | 前端 unit、完整 Vue 页面的浏览器 DOM 与浅深色截图 | 已配置模型、飞书/Dify/Notion 及加载旧快照时不显示提醒 | Passed |

实际执行：

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`：3525 passed，125 skipped；skip 不计入通过。
- `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q -p no:cacheprovider`：207 passed，补充宿主部署配置证据。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_background_jobs_router.py -k 'missing_embedding or cancel_http' -q -p no:cacheprovider`：2 passed。测试只创建并清理自身资源，真实 worker 失败后独立读取 PostgreSQL。
- `docker compose exec -T frontend pnpm run test:unit`：1011 passed；`pnpm run lint:check` 与 `pnpm run build` 在同一前端容器中通过。
- `playwright-cli -s=embedding-warning run-code --filename=frontend/test/browser/knowledgeEmbeddingWarning.js`：完整页面使用受控 API，浅深色各覆盖 1440/768 宽度，缺少模型提醒、正常配置隐藏、连接器隐藏、旧快照隐藏和切标签保持提醒均通过。浏览器回放不替代前述真实 HTTP/worker 证据。
- `python3 scripts/ci/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.ci.test_verify_engineering_contracts`：通过，后者 56 项。
- `pnpm --dir docs run build`：通过，包含文档链接检查；`git diff --check`：通过。

未执行真实 Embedding 服务调用：本需求验证缺少配置时的失败说明，不验证模型服务质量或修复历史配置。旧任务记录不回填公开原因。
