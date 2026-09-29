# 知识库文件夹同级同名防重与创建后进入

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/base.py

## 问题

知识库同一父目录下可以创建任意多个同名文件夹：前端只校验名称非空，`KnowledgeBase.create_folder` 与 `rename_folder` 不查同级重名，`knowledge_files` 表也没有 `(kb_id, parent_id, filename)` 唯一约束。单层浏览 + 面包屑的交互下，同名文件夹无法区分。另外创建文件夹后视图停留在父目录，上传默认目标仍是父目录，用户需要手动进入新文件夹。

## 决策

`create_folder` 与 `rename_folder` 在持久化前通过 `KnowledgeFileRepository.find_folder_by_name` 按 `kb_id + parent_id + 忽略大小写的 filename` 在 `is_folder=True` 记录中查重；命中（重命名时排除自身）即抛出 `FolderNameConflictError`。两个端点的路由把该异常映射为 409，detail 使用前端受控透传契约 `{"code": "folder_name_conflict", "message": ...}`，用户可见具体冲突名称。创建端点同时补齐与重命名一致的名称规范化（去空白、拒绝空名与路径分隔符），其他 `ValueError` 映射为 400。

前端 `FileTable.vue` 创建成功后直接 `openFolder` 进入新文件夹；上传弹窗的默认目标跟随当前浏览目录，因此新文件夹自动成为上传目标，不引入额外状态。

## 替代方案

- 数据库层加 `(kb_id, parent_id, filename)` 部分唯一约束：能防并发写入竞态，但历史数据可能已存在同名文件夹，约束迁移需要先清理存量；且文件夹与文件混表，文件名本身允许重复。当前并发创建同名文件夹的后果只是多一个可删除的目录，接受应用层检查。
- 树形全量展示子文件夹：上游 maintainer 明确选择 VS Code 式当前层级浏览（xerrors/Yuxi#617），不改动交互模型。
- 创建后仅把新文件夹设为上传弹窗默认值而不切换视图：需要在弹窗与表格间新增状态通道；直接进入新文件夹复用现有"上传默认目标 = 当前目录"语义，成本更低。

## 后果

同级重名创建与重命名返回 409，前端透传中文冲突提示；重命名为自身当前名称不受影响；不同父目录下的同名文件夹仍然合法。历史已存在的同名文件夹不受影响，只在再次重命名撞名时被拒绝。创建文件夹后视图进入新文件夹，面包屑追加一级；上传默认目标随之变为新文件夹。文件夹与同名文件不冲突（文件查重沿用内容 hash 逻辑，不在本决策范围）。

## 验证

- `docker compose exec api uv run --no-sync --group test pytest test/unit/knowledge/test_folder_name_conflict.py`：Passed（4 项）；负向覆盖同级重名创建拒绝且不落库、名称规范化、空名/分隔符拒绝、重命名撞名拒绝与自身放行。
- `docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow"`：Passed（2226 passed, 54 skipped）。
- `docker compose exec api uv run --no-sync --group test pytest test/integration/api/test_knowledge_router.py -k duplicate_sibling`：Not run（环境未配置 TEST_USERNAME/TEST_PASSWORD，用例按约定 skip）；该真实 HTTP + PostgreSQL 负向用例需在配置凭据的环境执行。
- `pnpm exec node --test --test-concurrency=1 test/unit/file_upload_folder.test.js`：Passed（5 项）；前端创建后进入新文件夹的源码契约。
- `pnpm run lint:check`、`pnpm run build`：Passed。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：Passed。
- 真实页面验证：Passed；用户确认同级同名文件夹创建被拒绝并透出冲突提示。
